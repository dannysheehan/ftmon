REM
REM Ensures NT.dll & NT.pm are in the locations expected by FTMON
REM
copy NT.pm ..\NT.pm
mkdir ..\..\mswin32
copy blib\arch\auto\FTMON\NT\NT.dll ..\..\mswin32
