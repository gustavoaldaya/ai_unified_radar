"""Ejecuta ext-bedrock-traces escribiendo al raw zone LOCAL (_local_raw), para
que build_star_pg.py (que lee local) lo cargue. extractors.run usa build_backend
-> ADLS en modo live; aqui se usa el backend por defecto (LocalStorageBackend),
igual que los scripts de backfill."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from extractors.run import _load_dotenv

_load_dotenv()

from extractors.bedrock_traces import BedrockTracesExtractor

print(BedrockTracesExtractor().run())