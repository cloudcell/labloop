#!/usr/bin/env python3
"""Seed Zoo Code's globalState with the lab's provider defaults.

Zoo Code (Cline-family) keeps its API-provider config in VSCode's
global state DB — state.vscdb — not in a file. This writes the
openai-compatible provider pointing at the host vLLM endpoint so every
clone is preconfigured. It MERGES: keys already present are never
overwritten (the user's own choices always win). No model id is seeded
— the user picks from the endpoint's /v1/models list; model names are
operator-private and don't belong in a template.

Run as the `lab` user inside the guest.
"""
import json, os, sqlite3

DB = os.path.expanduser(
    "~/.config/VSCodium/User/globalStorage/state.vscdb")
KEY = "ZooCodeOrganization.zoo-code"

SEED = {
    "apiProvider": "openai",
    "openAiBaseUrl": "http://192.168.122.1:8002/v1",
    "openAiCustomModelInfo": {
        "maxTokens": -1,
        "contextWindow": 128000,
        "supportsImages": True,
        "supportsPromptCache": False,
        "inputPrice": 0,
        "outputPrice": 0,
        "reasoningEffort": "medium",
    },
    "enableReasoningEffort": True,
    "telemetrySetting": "disabled",
    "listApiConfigMeta": [
        {"name": "default", "id": "labdefault",
         "apiProvider": "openai"}
    ],
    "currentApiConfigName": "default",
    "mode": "code",
    "allowedCommands": ["git log", "git diff", "git show"],
}

os.makedirs(os.path.dirname(DB), exist_ok=True)
db = sqlite3.connect(DB)
db.execute("CREATE TABLE IF NOT EXISTS ItemTable "
           "(key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
row = db.execute(
    "SELECT value FROM ItemTable WHERE key=?", (KEY,)).fetchone()
state = json.loads(row[0]) if row else {}
added = [k for k in SEED if k not in state]
for k in added:
    state[k] = SEED[k]
db.execute("INSERT OR REPLACE INTO ItemTable (key, value) "
           "VALUES (?, ?)", (KEY, json.dumps(state)))
db.commit()
print(f"zoo-code provider seeded ({len(added)} keys added, "
      f"{len(state)-len(added)} kept): {SEED['openAiBaseUrl']}")
