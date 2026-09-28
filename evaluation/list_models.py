"""Read available model identifiers without printing credentials."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
import httpx
from app.translation import configuration
load_dotenv(Path(__file__).resolve().parents[1] / '.env')
key, _ = configuration()
r = httpx.get('https://generativelanguage.googleapis.com/v1beta/models', headers={'x-goog-api-key':key}, timeout=30)
print('HTTP',r.status_code)
if r.status_code == 200:
    for model in r.json().get('models',[]):
        if 'generateContent' in model.get('supportedGenerationMethods',[]):
            print(model['name'])
