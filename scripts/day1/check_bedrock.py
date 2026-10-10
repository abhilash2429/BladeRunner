from openai import OpenAI

from common.config import env, load_env

load_env()

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_value",
        "description": "Look up a value by key.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
}]
MODELS = ["minimax.minimax-m2.5", "moonshotai.kimi-k2.5", "zai.glm-5", "deepseek.v3.2", "zai.glm-4.7-flash"]

client = OpenAI(base_url=env("BEDROCK_OPENAI_BASE_URL"), api_key=env("AWS_BEARER_TOKEN_BEDROCK"))
for model in MODELS:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call get_value with key 'alpha'."}],
            tools=TOOLS,
            max_tokens=300,
        )
        msg = r.choices[0].message
        kind = "tool_calls" if msg.tool_calls else "text"
        print(f"{model}: OK {kind} usage={r.usage}")
    except Exception as e:
        print(f"{model}: FAIL {type(e).__name__} {str(e)[:300]}")

try:
    import boto3

    rt = boto3.client("bedrock-runtime", region_name="us-east-1")
    out = rt.converse(
        modelId="minimax.minimax-m2.5",
        messages=[{"role": "user", "content": [{"text": "Say ok."}]}],
        inferenceConfig={"maxTokens": 20},
    )
    print("converse minimax: OK", out["usage"])
except Exception as e:
    print("converse minimax: FAIL", type(e).__name__, str(e)[:300])
