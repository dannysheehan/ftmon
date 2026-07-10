REM
REM Compiles event messages into dll file
REM
mc ftmon.mc
rc /r ftmon.rc
link /nodefaultlib /INCREMENTAL:NO /release /nologo -base:0x60000000 -machine:I386 -dll -noentry -out:ftmon.dll ftmon.res
