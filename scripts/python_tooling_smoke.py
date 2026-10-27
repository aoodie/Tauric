"""Exercise real pip, private libraries and offline venv bootstrap in the image."""

import argparse
import json
import subprocess

PROBE = '''import importlib.util,json,pathlib,subprocess,tempfile,venv,zipfile
from pip._vendor import msgpack,urllib3
assert msgpack.version==(1,2,1) and urllib3.__version__=="2.8.0"
assert msgpack.unpackb(msgpack.packb({"verified":True}))=={"verified":True}
assert importlib.util.find_spec("pip._vendor.pkg_resources") is None
with tempfile.TemporaryDirectory() as tmp:
 p=pathlib.Path(tmp)
 venv.EnvBuilder(with_pip=True,symlinks=True).create(p/"venv")
 python=str(p/"venv/bin/python")
 code="import os;os.environ['_PIP_USE_IMPORTLIB_METADATA']='0';from pip._vendor import msgpack,urllib3;assert msgpack.version==(1,2,1);assert urllib3.__version__=='2.8.0';from pip._internal.metadata import select_backend;assert select_backend().NAME=='importlib'"
 subprocess.run([python,"-I","-c",code],check=True,timeout=20)
 wheel=p/"tauric_fixture-1.0-py3-none-any.whl"
 with zipfile.ZipFile(wheel,"w") as z:
  z.writestr("tauric_fixture.py","value=42\\n")
  z.writestr("tauric_fixture-1.0.dist-info/METADATA","Metadata-Version: 2.1\\nName: tauric-fixture\\nVersion: 1.0\\n")
  z.writestr("tauric_fixture-1.0.dist-info/WHEEL","Wheel-Version: 1.0\\nGenerator: Tauric fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
  z.writestr("tauric_fixture-1.0.dist-info/RECORD","")
 subprocess.run([python,"-I","-m","pip","--isolated","install","--no-index","--no-deps",str(wheel)],check=True,stdout=subprocess.DEVNULL,timeout=20)
 subprocess.run([python,"-I","-c","import tauric_fixture;assert tauric_fixture.value==42"],check=True,timeout=10)
 subprocess.run([python,"-I","-m","pip","--isolated","check"],check=True,stdout=subprocess.DEVNULL,timeout=20)
print(json.dumps({"passed":True,"checks":["private_library_versions","msgpack_round_trip","legacy_backend_removed","venv_bootstrap","bootstrap_libraries","metadata_backend","offline_install_and_import","pip_check"]}))
'''


def verify(image):
    command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true', '--user', '10001:10001',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=268435456', '--entrypoint', 'python', image,
               '-I', '-c', PROBE]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('Python tooling verification failed: ' + result.stderr[-4000:])
    report = json.loads(result.stdout)
    if report.get('passed') is not True or len(report.get('checks', [])) != 8:
        raise ValueError('The image did not execute every required Python tooling check.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.image)))


if __name__ == '__main__':
    main()
