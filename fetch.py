import urllib.request
html = urllib.request.urlopen("https://nobe.moje-autoskola.cz/index.php").read().decode("utf-8")
with open("test.html", "w", encoding="utf-8") as f:
    f.write(html)
