package FTMON::EventLogScraper;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: EventLogScraper.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Responsible for extracting events from NT application logs based on 
#   @(#) a user defined scraper function.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventLogScraper.pm,v $
#
#   $Date: 2003/01/10 13:10:56 $
#
#   @(#) $Revision: 1.1.1.1 $
#    
#
#   Copyright (C) 2001  Danny Sheehan
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
#      FTMON
#      PO Box 283
#      Sydney NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use TraceFuncs;
use Win32::EventLog;
use FTMON::Base;
use FTMON::Scheduler;

# ----------------------------------------------------------------------

$DEBUG = 0 if ( ! defined($FTMON::EventLogScraper::DEBUG) );

@FTMON::EventLogScraper::ISA = ("FTMON::Base");

%EventType =    (0,  'Error',
                 1,  'Error',
                 2,  'Warning',
                 3,  'Warning',
                 4,  'Information',
                 8,  'Audit success',
                 16,  'Audit failure');

my %ScraperList = ();

my %Packages = ();
my @Files    = ();
my @LogFiles = ();

my($NAME,
   $POSN) = ( 0 .. 1 );

my($SCRAPER,
   $EVENTS) = ( 0 .. 1 );

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 3;
my($FILES,
   $PACKAGES,
   $_SCRAPER_LIST,
  ) = 
     ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# -------------------------------------------------------------------------
#
# CLASS Method only.
#
sub find_scraper
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $name = shift;

  if ( exists($ScraperList{$name}) )
  {
    $DEBUG && TraceFuncs::debug("Found");
    return($ScraperList{$name});
  }
  else
  {
    $DEBUG && TraceFuncs::debug("NotFound");
    return(undef);
  }
}

# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  my $name = shift;
  my $files = shift;

  my $class = ref($proto) || $proto;

  my $self = $class->SUPER::new($name);

  my $scraper = [];
  $scraper = FTMON::EventLogScraper::find_scraper($name);
  if ( defined($scraper) )
  {
    return($scraper);
  }


  $self->[$_SCRAPER_LIST] = \%ScraperList;
  $self->[$NAME]     = $name;
  $self->[$PACKAGES] = \%Packages;
  $self->[$FILES]    = \@Files;

  bless($self, $class);

  $self->files($files);

  $self->[$_SCRAPER_LIST]->{$name} = $self;

  sub timer_sub  { 1 };
  sub scrape_sub { my $object = shift; $object->scrape() };

  my $job = 
       FTMON::Job->new(
         "ACTION: $name $class",
         \&timer_sub,
         \&scrape_sub,
         $FT::HOSTNAME,
         "Scrapes NT Event Log for critical events",
         1,
         $self);
  $FTMON::Scheduler::SINGLETON->at($job);

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}

# -------------------------------------------------------------------------
sub scrape_log
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $name = shift;
  my $logs = shift;
  my $scraper = shift;

  my $s = FTMON::EventLogScraper->new($name, $logs);
  $s->register($scraper);
  $s->get_events();
}


# ----------------------------------------------------------------------
my $_INIT_SUB = sub 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $file = shift;
  my $computer_name = shift;

  my $current_position = -1;

  my $handle;
  my $recs;
  my $base;

  $handle=Win32::EventLog->new($file, $computer_name)
      or die "Can't open $file EventLog\n";

  $handle->GetNumber($recs)
      or die "Can't get number of EventLog records\n";

  $handle->GetOldest($base)
      or die "Can't get number of oldest EventLog record\n";
      
  $current_position = $base + $recs;


  $DEBUG && TraceFuncs::debug("current_position = $current_position");
  return($current_position);
};


# ----------------------------------------------------------------------
sub files
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $data;
  my $files;
  
  if ( @_ )
  {
    $files = shift;

    # REVISIT print "You must specify at least one log" if ( ! @{$files} );
    foreach $file ( @{$files} )
    {
       next if ( ! $file );
       push( @{$self->[$FILES]}, [ $file, $_INIT_SUB->($file) ] );
      $DEBUG && TraceFuncs::debug("file = " . $file);
    }
  }

  return($self->[$FILES]);
}

# ----------------------------------------------------------------------
# 06-27-2001  03:47:58  Local1.Notice  172.30.17.1  dsheehan: ppp:IPCP Closing

my $_SCRAPE = sub 
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $file = shift;
  my $data = shift;
  my $hostname = shift;

  my $file_name = $file->[$NAME];
  my %events = ();
  my %repeat_counts = ();
  my @match = ();
  my @parameters = ();
  my $parameters;

  $FTMON::EventLogScraper::FILE = $file_name;
  $DEBUG && TraceFuncs::debug("scrape $file_name");

  use Win32::EventLog
  my $recs = 0;
  my $first = 0;
  my $handle= new Win32::EventLog($file_name, $hostname)
      or die "Can't open $file EventLog\n";

  $handle->GetNumber($recs)
      or die "Can't get number of EventLog records\n";
  $handle->GetOldest($first)
      or die "Can't get first EventLog record\n";

  my $last = $file->[$POSN];
  while ($last < $recs) 
  {
    $handle->Read(EVENTLOG_FORWARDS_READ|EVENTLOG_SEEK_READ,
       $last,
       $hashRef) || die "Can't read EventLog entry #$last of $recs\n";


    Win32::EventLog::GetMessageText($hashRef);
    my $message = $hashRef->{'Message'};

    ($sec,$min,$hour,$mday,$mon,$year,$sday,$yday,$isdst) = 
       localtime($hashRef->{'TimeGenerated'});;
    $time_str = sprintf("%02d\-%02d\-%02d %02d:%02d:%02d",
                          $year + 1900,$mon+1,$mday,$hour,$min,$sec);
    $event_type = $hashRef->{'EventType'};
    $event_type = $FTMON::EventLogScraper::EventType{$event_type};

    $id = $hashRef->{'EventID'} & 0xffff;
    $parameters = $hashRef->{'Strings'};
    @parameters = split("\0", $parameters);

    # next if ( $hashRef->{'Source'} eq "FTMON" );

    $_ = { "Date", $time_str,
           "Computer", $hashRef->{'Computer'},
           "Category", $hashRef->{'Category'},
           "EventType", $event_type,
           "SID", $hashRef->{'User'},
           "Source", $hashRef->{'Source'},
           "ID", $id,
           "Message", $message,
           "Parameters", [ @parameters ] };
print "|||| $_ \n";
    if ( $data->[$SCRAPER]->(\%events, \%repeat_counts) )
    {
      $DEBUG && TraceFuncs::debug("match at line $.");
    }

    $last++;
  }
  $handle->Close();

  $file->[$POSN]   = $last;
  while ( ($key, $value) = each %events )
  {
    $data->[$EVENTS]->{$key} = $value;
  }

};

# ----------------------------------------------------------------------
sub get_events
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $package = shift;

  $package = $FT::PACKAGE if ( ! defined($package) );

  @FT::VALUES = values %{$self->[$PACKAGES]->{$package}->[$EVENTS]};
}

# ----------------------------------------------------------------------
sub scrape
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $file;
  my $scraper;

  while ( ($package, $data) = each(%{$self->[$PACKAGES]}) )
  {
    $data->[$EVENTS] = undef;
    foreach $file ( @{$self->[$FILES]} )
    {
      $_SCRAPE->($file, $data);
    }
  }
}

# ----------------------------------------------------------------------
sub register
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self    = shift;
  my $scraper = shift;
  my $package = shift;

  $package = $FT::PACKAGE if ( ! defined($package) );

  if ( ! exists($self->[$PACKAGES]->{$package}) )
  {
    $self->[$PACKAGES]->{$package} = [ $scraper, {} ];
  }
}

1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::EventLogScraper - 

=head1 SYNOPSIS

See METHODS section below

=head1 DESCRIPTION

FTMON::Base

=head1 METHODS

=head1 SEE ALSO

=head1 EXAMPLES

=head1 AUTHOR

Danny Sheehan <dsheehan@ftmon.org>

=cut
