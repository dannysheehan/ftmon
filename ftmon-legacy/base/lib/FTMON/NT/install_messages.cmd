REM
REM install event messages 
REM

REM
REM NB Ensure that Onepoint and eventvwr are not running if copy dosn't work.
REM
copy ftmon.dll %SystemRoot%\System32
%SystemRoot%\regedit /s ftmon.reg
echo ftmon.dll registered
