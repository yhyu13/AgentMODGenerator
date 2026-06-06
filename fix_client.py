"""Apply thinking block fix to llm/client.py."""
import re

path = '/home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator/llm/client.py'
with open(path) as f:
    content = f.read()

OLD = '''def _strip_code_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```", 2)
        if len(parts) >= 3:
            content = parts[1]
            content = content.lstrip("json").strip()
    return content'''

NEW = '''def _strip_code_fence(content: str) -> str:
    import re
    content = content.strip()
    # Remove thinking blocks: <think>...(multi-line)...</think>
    content = re.sub(r'</think>.*?</think>', '', content, flags=re.DOTALL)
    if content.startswith("```"):
        parts = content.split("```", 2)
        if len(parts) >= 3:
            content = parts[1]
            content = content.lstrip("json").strip()
    return content'''

if OLD not in content:
    print("ERROR: OLD pattern not found")
    idx = content.find('def _strip_code_fence')
    print(repr(content[idx:idx+200]))
    exit(1)

content = content.replace(OLD, NEW, 1)
with open(path, 'w') as f:
    f.write(content)

print("SUCCESS - fixed thinking block stripping in _strip_code_fence")
