# SPDX-License-Identifier: GPL-3.0-or-later
# PowerShell wrapper; no installation or system configuration changes.
$ErrorActionPreference = 'Stop'
$driver = Join-Path $PSScriptRoot 'build.py'
& python $driver @args
exit $LASTEXITCODE
