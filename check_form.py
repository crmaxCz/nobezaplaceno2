import urllib.request
import re

r = urllib.request.urlopen('https://nobe.moje-autoskola.cz/index.php')
html = r.read().decode('utf-8', errors='replace')

# Find all input fields
inputs = re.findall(r'<input[^>]*>', html, re.IGNORECASE)
for inp in inputs:
    print(inp)

print("---FORMS---")
forms = re.findall(r'<form[^>]*>', html, re.IGNORECASE)
for f in forms:
    print(f)
