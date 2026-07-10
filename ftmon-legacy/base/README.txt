PRE-REQS
- Active-State perl

I have included some modules that are not part of the Standard Active State 
distribution to speed up development (i.e. it is a pain to source them).
They are not FTMON code and should't really be inlcuded here but are used
by FTMON. If i have offended the authors in any way they will be removed and i 
will replace with instructions on how to source them:
	BER.pm
	RRDTOOL (compiled libraries)
	Crypt/CipherSaber.pm
	NET/Telnet.pm
	SNMP_Session.pm
	Postemsg.pm
	SNMP_util.pm
	TraceFuncs.pm
	Shell.pm
	Win32/API.pm
(please do not modify the above files or consider them as part of the FTMON 
distribution).
These files are contained in lib.tar.gz (Lib.zip). Just extract them into the
lib directory.

BINARY DISTRIBUTIONS
- Using the Active State perl development kit you can make an installable
image of FTMON for the WINNT, Solaris and Linux platforms. This makes
for easier deployment i.e. you don't have to install perl and associated 
libraries.
- To compile FTMON yourself you will need a licensed copy of the PDK from
Active State. Images are available on ftmon.org if this is not an 
option. In addition to compile on NT you will need access to a C compiler
for the perl xs component.

DOCUMENTATION
- I have a reputation for being a hack coder so as usuall doco. is an
afterthought. It is getting there. I am also interested in how intuitive the
configuration language is (when using the examples), well that is my excuse anyway.

