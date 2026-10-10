import httpx
from openai import OpenAI

from common.config import env, load_env

load_env()
base = env("LITELLM_BASE_URL")
key = env("LITELLM_API_KEY")
headers = {"Authorization": f"Bearer {key}"}

models = httpx.get(f"{base}/models", headers=headers, timeout=30)
print("models:", models.status_code, [m["id"] for m in models.json().get("data", [])])

client = OpenAI(base_url=base, api_key=key)
r = client.chat.completions.create(
    model="claude-sonnet-4.6",
    messages=[{"role": "user", "content": "Call get_value with key 'alpha'."}],
    tools=[{"type": "function", "function": {"name": "get_value", "description": "Look up a value.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}}],
    max_tokens=200,
)
print("sonnet tool_calls:", bool(r.choices[0].message.tool_calls), r.usage)

root = base.removesuffix("/v1")
info = httpx.get(f"{root}/key/info", headers=headers, timeout=30)
print("key/info:", info.status_code, info.text[:500])
