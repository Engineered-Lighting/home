"""Quick check what tools are in the live subentry."""
import json
import yaml

with open("C:/Claude/home/tools/_tmp/ce.json", encoding="utf-8") as f:
    d = json.load(f)

for e in d["data"]["entries"]:
    if e.get("domain") == "extended_openai_conversation":
        for s in e.get("subentries", []):
            ft = s["data"].get("function_tools", "")
            if ft:
                tools = yaml.safe_load(ft)
                names = sorted([t.get("spec", {}).get("name") for t in tools if isinstance(t, dict)])
                print(f"subentry {s.get('subentry_id')} has {len(names)} tools:")
                for n in names:
                    marker = "  ← NEW" if n in ("set_presence_override", "clear_presence_override") else ""
                    print(f"  {n}{marker}")
