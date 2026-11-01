"""Local BGE-M3 dense embeddings, CLS pooling + L2, no remote code or API."""
from pathlib import Path
import hashlib
import json
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env', override=False)
MODEL_ID = "BAAI/bge-m3"
MAX_INPUT_TOKENS = 384


def model_path():
    explicit = os.environ.get("FIN_RAG_MODEL_PATH")
    if explicit:
        p = Path(explicit).expanduser().resolve()
    else:
        hub = Path.home() / ".cache/huggingface/hub/models--BAAI--bge-m3"
        revision = (hub / "refs/main").read_text().strip()
        p = hub / "snapshots" / revision
    if not (p / "onnx/model.onnx").exists():
        raise RuntimeError("BGE-M3 ONNX 缓存不存在，请配置 FIN_RAG_MODEL_PATH")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def tokenizer():
    from tokenizers import Tokenizer
    t = Tokenizer.from_file(str(model_path() / "onnx/tokenizer.json"))
    t.no_truncation()
    t.no_padding()
    return t


class LocalEmbeddingClient:
    """Also implements the original extractor's embed_texts(texts) protocol."""
    model = MODEL_ID

    def __init__(self, threads=3):
        import onnxruntime as ort
        self.path = model_path()
        self.tokenizer = tokenizer()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        # Avoid simultaneously keeping multiple optimized copies of the 2.2GB model.
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(self.path / "onnx/model.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"])
        self.inputs = {x.name for x in self.session.get_inputs()}
        self.outputs = [x.name for x in self.session.get_outputs()]

    def embed_texts(self, texts):
        import numpy as np
        encoded = self.tokenizer.encode_batch(texts)
        if not encoded:
            return []
        if max(len(e.ids) for e in encoded) > MAX_INPUT_TOKENS:
            raise ValueError("输入超过试点 token 上限；必须先切分，禁止静默截断")
        width = max(len(e.ids) for e in encoded)
        ids = np.full((len(encoded), width), 1, dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, e in enumerate(encoded):
            ids[i, :len(e.ids)] = e.ids
            mask[i, :len(e.ids)] = 1
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)
        out = self.session.run(None, {k:v for k,v in feeds.items() if k in self.inputs})
        # Official cached export returns token embeddings; BGE-M3 dense uses CLS.
        candidates = [x for x in out if x.ndim == 3 and x.shape[-1] == 1024]
        if not candidates:
            raise RuntimeError("ONNX 输出与已核验的 BGE-M3 token embedding 结构不符")
        vectors = candidates[0][:, 0, :].astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if not np.isfinite(vectors).all() or (norms == 0).any():
            raise RuntimeError("模型产生无效向量")
        return (vectors / norms).tolist()

    def provenance(self):
        return {"model": MODEL_ID, "revision": self.path.name,
            "backend": "onnxruntime-cpu", "pooling": "cls", "normalized": True,
            "dimensions": 1024, "max_input_tokens": MAX_INPUT_TOKENS,
            "query_instruction": "", "model_path": str(self.path),
            "onnx_sha256": sha256_file(self.path / "onnx/model.onnx"),
            "external_weights_sha256": sha256_file(self.path / "onnx/model.onnx_data"),
            "tokenizer_sha256": sha256_file(self.path / "onnx/tokenizer.json"),
            "documentation": "https://huggingface.co/BAAI/bge-m3"}
