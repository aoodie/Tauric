"""Immutable black-box API checks, executed under a separate container identity."""

import hashlib
import json
import subprocess

PROFILE = "tauric_api_smoke_v1"
CASES = ["live_health", "database_readiness", "owner_authentication_required", "validation_redacts_password"]
PROBE = '''import json,time,urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(NoRedirect)
def request(path,body=None):
 data=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request("http://127.0.0.1:18000"+path,data=data,headers={"Content-Type":"application/json","Origin":"http://127.0.0.1:18000"})
 try:r=opener.open(req,timeout=2)
 except urllib.error.HTTPError as error:r=error
 with r:
  raw=r.read(2000000)
  return r.code,raw
deadline=time.monotonic()+20
while time.monotonic()<deadline:
 try:
  if request("/health/ready")[0]==200:break
 except (OSError,ValueError):pass
 time.sleep(.2)
cases=[]
def check(name,test):
 try:passed=bool(test())
 except Exception:passed=False
 cases.append({"name":name,"passed":passed})
def health(path):
 code,raw=request(path)
 return code==200 and json.loads(raw)=={"status":"ok"}
def private_validation():
 code,raw=request("/api/v1/session",{"password":"tauric-private-sentinel"})
 return code==422 and b"tauric-private-sentinel" not in raw and isinstance(json.loads(raw).get("detail"),list)
check("live_health",lambda:health("/health/live"))
check("database_readiness",lambda:health("/health/ready"))
check("owner_authentication_required",lambda:request("/api/v1/assistant/tasks")[0]==401)
check("validation_redacts_password",private_validation)
print(json.dumps({"cases":cases}))
'''


def verify(name):
    # -S avoids site/.pth imports, -I ignores workspace and environment paths.
    # The subject process runs as 10001 and cannot modify/impersonate 65534's
    # verifier, its stdout or the immutable host-supplied test inventory.
    command = ["docker", "exec", "--user", "65534:65534", "--workdir", "/",
               name, "python", "-I", "-S", "-c", PROBE]
    result = subprocess.run(command, capture_output=True, timeout=35, check=False)
    try:
        value = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) <= 8192 else {}
        cases = value["cases"]
        passed = [c["name"] for c in cases if c["passed"] is True]
        valid = [c["name"] for c in cases] == CASES
    except (ValueError, KeyError, TypeError):
        cases, passed, valid = [], [], False
    return {"profile": PROFILE, "inventory_sha256": hashlib.sha256(PROBE.encode()).hexdigest(),
            "cases": cases, "tests_executed": len(cases) if valid else 0,
            "passed": valid and passed == CASES}
