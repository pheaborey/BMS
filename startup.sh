#!/bin/sh
# Bring the Meridian BMS preview up. Idempotent.
if curl -sf -o /dev/null --max-time 1 http://127.0.0.1:8080/; then
  exit 0
fi
if ! python3 -c "import fastapi,uvicorn,jinja2,multipart" 2>/dev/null; then
  pip install -q -r /workspace/bms/requirements.txt
fi
cd /workspace/bms
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080 --log-level warning >> /tmp/bms.log 2>&1 &
i=0
while [ "$i" -lt 30 ]; do
  if curl -sf -o /dev/null --max-time 1 http://127.0.0.1:8080/; then
    exit 0
  fi
  i=$((i + 1))
  sleep 0.2
done
exit 0
