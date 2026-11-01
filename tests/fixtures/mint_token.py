"""Write a short-lived wiki-signed token to a file. The secret stays in the container."""
import base64, json, os, sys, time
from osw_chatbot.auth import sign
payload = {"u": "live-chat-test", "w": "mediawiki", "iat": time.time(),
           "exp": time.time() + 900, "n": "livechat",
           "a": ["code-sandbox"], "ca": []}
b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
open(sys.argv[1], "w").write(f"{b}.{sign(b, os.environ['CHATBOT_SHARED_SECRET'])}")
print("token written to", sys.argv[1])
