run this under ~/Documents/Gitrepo-My/AMG/sdv-mod-generator work
ALL_PROXY=socks5://127.0.0.1:1089 HTTPS_PROXY=http://127.0.0.1:8889 HTTP_PROXY=http://127.0.0.1:8889 PYTHONPATH=. uvicorn app.main:app --reload --port 8000

make run under ~/Documents/Gitrepo-My/AMG/sdv-mod-generator does not work
ALL_PROXY=socks5://127.0.0.1:1089 HTTPS_PROXY=http://127.0.0.1:8889 HTTP_PROXY=http://127.0.0.1:8889 PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000

cd /home/hangyu5/Documents/Gitrepo-My/AMG/sdv-mod-generator/ && ALL_PROXY=socks5://127.0.0.1:1089 HTTPS_PROXY=http://127.0.0.1:8889 HTTP_PROXY=http://127.0.0.1:8889 PYTHONPATH=. /home/hangyu5/Documents/Gitrepo-My/AMG/.venv/bin/uvicorn app.main:app --reload --port 8000