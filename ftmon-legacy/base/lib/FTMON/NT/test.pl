# Before `make install' is performed this script should be runnable with
# `make test'. After `make install' it should work as `perl test.pl'

#########################

# change 'tests => 1' to 'tests => last_test_to_print';

use Test;
BEGIN { plan tests => 1 };
use FTMON::NT;
use extUtils::testlib;

NT::PlaySound('d:/ftmon/sounds/barking dog.wav');
NT::SendMessage("sypwetc15", "testing");
exit(0);
my $results = NT::getUsers("sypwetc15");
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col |";
  }
  print "\n";
}
exit(0);
my $results = NT::getSessions("sypwetc15");
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col |";
  }
  print "\n";
}


my $results = NT::getApplications(1);
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col|";
  }
  print "\n";
}

my $results = NT::getProcesses("test");
foreach $row ( @{$results})
{
  
  foreach $col ( @{$row} )
  {
    print "$col|";
  }
  print "\n";
  
}

my $results = NT::getLogicalDrives("test", 0xff);
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col|";
  }
  print "drive_type = ", NT::driveTypeStr($row->[1]), "\n";
  print "\n";
}

@x = ('\Server\Work Item Shortages', '\System\Processes', '\System\Threads');
my $results = NT::getPerfCounters("test", \@x);
foreach $row ( @{$results})
{
  print $row, "|";
}

my @x = ("ID Process", "Elapsed Time", '% Processor Time');
my $results = NT::getPerfInstances("test", "Process", \@x);
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col|";
  }
  print "\n";
}
print "\n";


#
#my $results = NT::getPerfObjects("sypwetc15");
#foreach $row ( @{$results})
#{
#    print "$row\n";
#}

my $results = NT::getServices("");
foreach $row ( @{$results})
{
  foreach $col ( @{$row} )
  {
    print "$col|";
  }
  print "\n";
}
exit(0);
ok(1); # If we made it this far, we're ok.

#########################

# Insert your test code below, the Test module is use()ed here so read
# its man page ( perldoc Test ) for help writing this test script.

