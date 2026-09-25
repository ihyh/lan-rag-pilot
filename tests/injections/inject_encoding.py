"""Remove the UTF-8 BOM to prove the Windows script encoding guard catches it."""

TARGET = "scripts/start_local.ps1"
BOM = b"\xef\xbb\xbf"
EXPECT = "start_local.ps1：含非 ASCII，必须带 UTF-8 BOM"
