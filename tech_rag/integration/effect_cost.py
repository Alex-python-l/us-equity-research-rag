"""Persistent fail-closed accounting for the explicitly bounded A/B batch."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import uuid


class BudgetStop(RuntimeError):pass


class BatchBudget:
    def __init__(self,path,*,max_runs=6,per_run=12,max_requests=72,max_tokens=400000):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.limits=dict(max_runs=max_runs,per_run=per_run,max_requests=max_requests,max_tokens=max_tokens)

    @contextmanager
    def _locked(self):
        lock=self.path.with_suffix('.lock')
        try:fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:raise BudgetStop('budget ledger is in use; never launch a duplicate batch') from None
        try:yield
        finally:os.close(fd);lock.unlink()

    def read(self):
        if self.path.exists():return json.loads(self.path.read_text(encoding='utf-8'))
        return dict(schema_version='effect_v4_budget',limits=self.limits,runs={},requests=[],
                    request_count=0,total_tokens=0,usage_unknown=False)

    def _save(self,data):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.path)

    def start_run(self,run_id,metadata):
        with self._locked():
            data=self.read();limits=data['limits']
            if run_id in data['runs']:raise BudgetStop('run already reserved; automatic reruns are forbidden')
            if len(data['runs'])>=limits['max_runs']:raise BudgetStop('batch run limit')
            self._check(data)
            data['runs'][run_id]=dict(metadata=metadata,started_at=datetime.now(timezone.utc).isoformat(),status='started')
            self._save(data)

    @staticmethod
    def _check(data):
        if data['usage_unknown'] or any(r['status']=='pending' for r in data['requests']):
            raise BudgetStop('unknown/pending provider usage; no unaccounted follow-up request')
        if data['request_count']>=data['limits']['max_requests']:raise BudgetStop('batch request limit')
        if data['total_tokens']>=data['limits']['max_tokens']:raise BudgetStop('provider token threshold')

    def reserve(self,run_id,role):
        with self._locked():
            data=self.read();self._check(data)
            branch=data.get('branches',{}).get(run_id)
            if run_id not in data['runs'] and branch is None:raise BudgetStop('run must be reserved first')
            carried=len(branch['carried_request_ids']) if branch else 0
            if carried+sum(r['run_id']==run_id for r in data['requests'])>=data['limits']['per_run']:
                raise BudgetStop('per-run request limit')
            rid='REQ4_'+uuid.uuid4().hex
            data['requests'].append(dict(request_id=rid,run_id=run_id,role=role,status='pending',
                                         requested_at=datetime.now(timezone.utc).isoformat()))
            data['request_count']+=1;self._save(data)
            return rid

    def complete(self,request_id,usage,status,**metadata):
        with self._locked():
            data=self.read();row=next(r for r in data['requests'] if r['request_id']==request_id)
            if row['status']!='pending':raise BudgetStop('request already accounted')
            row.update(status=status,usage=usage,**metadata)
            total=(usage or {}).get('total_tokens')
            if type(total) is int and total>=0:data['total_tokens']+=total
            else:data['usage_unknown']=True
            self._save(data)

    def finish_run(self,run_id,status):
        with self._locked():
            data=self.read();data['runs'][run_id]['status']=status;self._save(data)

    def start_repair_branch(self,branch_id,owner_run_id,carried_request_ids):
        """One audited failed-prefix repair, not an additional complete case.

        Every discarded request remains charged globally. The corrected branch
        counts reused prefix requests toward its per-execution limit too.
        Completed primary cases can never be expanded beyond max_runs.
        """
        with self._locked():
            data=self.read();self._check(data)
            owner=data['runs'].get(owner_run_id,{})
            if owner.get('status')!='provider_or_budget_incomplete':raise BudgetStop('only incomplete cases may branch')
            branches=data.setdefault('branches',{})
            if branch_id in branches or any(v['owner_run_id']==owner_run_id for v in branches.values()):
                raise BudgetStop('a repair branch already exists; no repeated attempts')
            owned={r['request_id'] for r in data['requests'] if r['run_id']==owner_run_id and r['status']=='ok'}
            if len(set(carried_request_ids))!=len(carried_request_ids) or not set(carried_request_ids)<=owned:
                raise BudgetStop('invalid reusable request prefix')
            branches[branch_id]=dict(owner_run_id=owner_run_id,carried_request_ids=carried_request_ids,status='started',
                started_at=datetime.now(timezone.utc).isoformat(),reason='lossless context repair; original branch retained')
            self._save(data)


class RepairBranchBudget:
    def __init__(self,budget,branch_id,owner):self.budget=budget;self.branch_id=branch_id;self.owner=owner
    def reserve(self,run_id,role):
        if run_id!=self.owner:raise BudgetStop('repair case identity mismatch')
        return self.budget.reserve(self.branch_id,role)
    def complete(self,*args,**kwargs):return self.budget.complete(*args,**kwargs)
