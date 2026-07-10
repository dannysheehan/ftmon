package FTMON::LogFileScraper;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: LogFileScraper.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Extracts system events from a logfiles/logfiles based on a user
#   @(#) defined scraper function.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/LogFileScraper.pm,v $
#
#   $Date: 2003/01/10 13:10:52 $
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
use FTMON::Scheduler;
use FTMON::Base;

# ----------------------------------------------------------------------
  $DEBUG = 0 if ( ! defined($FTMON::LogFileScraper::DEBUG) );

  @FTMON::LogFileScraper::ISA = ("FTMON::Base");

  $ID = "";
  $PACKAGE = "";

  my %ScraperList = ();

  my($NAME,
     $POSN) = ( 0 .. 1 );

  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 7;
  my($SCRAPER_NAME,
     $FILES,
     $OWNER_PKG,
     $SCRAPER_LIST,
     $EVENTS,
     $SCRAPE_INTERVAL,
     $SCRAPER,) = 
        ( $FTMON::Base::_LAST_ATTRIB + 1 ..  $_LAST_ATTRIB );

  # ----------------------------------------------------------------------
  sub new
  {
    my $proto  = shift;
    my $name = shift;
    my $files = shift;
    my $scraper_sub = shift;
    my $scrape_interval = shift;

    my $class = ref($proto) || $proto;

    $name = "unknown" if ( ! defined $name );


    my $scraper = [];
    $scraper = FTMON::LogFileScraper::find_scraper($name);
    if ( defined($scraper) )
    {
      return($scraper);
    }


    die "You must define a scraper" if ( ! defined $scraper_sub );

    my $self = $class->SUPER::new($name);

    $self->[$SCRAPER_LIST] = \%ScraperList;
    $self->[$SCRAPER_NAME]     = $name;
    $self->[$OWNER_PKG]     = $FT::PACKAGE;
    $self->[$FILES]    = [];
    $self->[$EVENTS]    = {};

    $self->[$SCRAPER_SUB]    = $scraper_sub;
    $scrape_interval = 1 if ( ! defined $scrape_interval );
    $self->[$SCRAPE_INTERVAL]    = $scrape_interval;

    bless($self, $class);

    $self->files($files);

    $self->[$SCRAPER_LIST]->{$name} = $self;


    sub timer_sub { FT::timer($scrape_interval) };
    sub scraper_sub { my $object = shift; $object->scrape() };
    my $job = 
         FTMON::Job->new(
           "ACTION: ($FT::PACKAGE) " . $name,
           \&timer_sub,
           \&scraper_sub,
           $FT::HOSTNAME,
           "$FT::PACKAGE : Scrapes logfiles for critical events",
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
    my $interval = shift;

    $FTMON::LogFileScraper::ID = $name;

    my $s = FTMON::LogFileScraper->new($name, $logs, $scraper, $interval);
    $s->get_events();
  }

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
  my $_INIT_SUB = sub 
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $file = shift;
    my $current_position = -1;

    #
    # Skip to end of logfile to start from fresh.
    #
    open(LOG, "< $file") || die "Could not open $file - $!";
    while( <LOG> )
    {
      ;#
      $current_position = $.;
    }

    $DEBUG && TraceFuncs::debug("current_position = $current_position");
    close(LOG) || die "Could not close $file - $!";
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

      # REVISIT print "You must specify at least one file" if ( ! @{$files} );
      foreach $file ( @{$files} )
      {
  # REVISIT: Not sure why this problem occurs (with multiple files).
        next if ( ! $file );
         push( @{$self->[$FILES]}, [ $file, $_INIT_SUB->($file) ] );
        $DEBUG && TraceFuncs::debug("file = " . $file);
      }
    }

    return($self->[$FILES]);
  }

  # ----------------------------------------------------------------------
  my $_SCRAPE = sub 
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $file = shift;
    my $scraper = shift;
    my $events = shift;

    my $file_name = $file->[$NAME];
    my @match = ();

    $FTMON::LogFileScraper::FILE = $file_name;
    $FTMON::LogFileScraper::FILE_ID = $FT::LOOKUP{$file_name} || $file_name;
    $DEBUG && TraceFuncs::debug("scrape $FTMON::LogFileScraper::FILE");

    #
    # Do a forced scrape - incase we want to carry updates over
    #
    $_ = "";
    $scraper->(\%events, \%repeat_counts);

    $DEBUG && TraceFuncs::debug("open " . $file_name);
    open(LOG, $file_name) || die "Could not open $file_name - $!";
    my $last = 0;
    $DEBUG && TraceFuncs::debug("last file position " . $file->[$POSN]);
    while ( <LOG> )
    {
      if ( $. > $file->[$POSN] )
      {
        if ( ! /FTMON/ && $scraper->(\%events, \%repeat_counts) )
        {
          $DEBUG && TraceFuncs::debug("match at line $.");
        }
        $last = $.;
	last;
      }
    }

    if ( $. < $file->[$POSN] )
    {
      $DEBUG && TraceFuncs::debug("File rolled over");
      close(LOG);
      $. = 0;
      $file->[$POSN] = -1;
      open(LOG, $file_name) || die "Could not open $file_name - $!";
    }

    while ( <LOG> )
    {
      $DEBUG && TraceFuncs::debug("line $. \n");
      $DEBUG && TraceFuncs::debug("line is $_ \n");
      if ( ! /FTMON/ && $scraper->(\%events, \%repeat_counts) )
      {
        $DEBUG && TraceFuncs::debug("match at line $.");
      }
      $last = $.;
    }
    $file->[$POSN]  = $last if ( $last );
    close(LOG) || die "Could not close $file_name - $!";


    my $key;
    my $value;
    while ( ($key, $value) = each %events )
    {
      $$events->{$key} = $value;
    }
  };

  # ----------------------------------------------------------------------
  sub get_events
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $package = shift;

    $FTMON::LogFileScraper::ID = $self->[$SCRAPER_NAME];
    $FTMON::LogFileScraper::PACKAGE = $self->[$OWNER_PKG];
    $package = $FT::PACKAGE if ( ! defined($package) );

    $DEBUG && TraceFuncs::debug("package = $FTMON::LogFileScraper::PACKAGE");
    $DEBUG && TraceFuncs::debug("id = $FTMON::LogFileScraper::ID");
    @FT::VALUES = values %{$self->[$EVENTS]};
    my $num_events = @FT::VALUES;
    $DEBUG && TraceFuncs::debug("num events = $num_events");
  }

  # ----------------------------------------------------------------------
  sub scrape
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $file;

    # REVISIT
    %events = ();
    %repeat_counts = ();

    $FTMON::LogFileScraper::ID = $self->[$SCRAPER_NAME];
    return if ( ! defined $self->[$OWNER_PKG] || ! $self->[$OWNER_PKG] ); 

    $FTMON::LogFileScraper::PACKAGE = $self->[$OWNER_PKG];
    $DEBUG && TraceFuncs::debug("owner package = " . $self->[$OWNER_PKG]);
    $DEBUG && TraceFuncs::debug("package = " . $FT::PACKAGE);

    $self->[$EVENTS] = undef;
    foreach $file ( @{$self->[$FILES]} )
    {
      $DEBUG && TraceFuncs::debug("file = $file");
      $_SCRAPE->($file, $self->[$SCRAPER_SUB], \$self->[$EVENTS]);
      $DEBUG && TraceFuncs::debug("after scrape");
    }
    my @my_events = values %{$self->[$EVENTS]};
    my $num_events = @my_events;
    $DEBUG && TraceFuncs::debug("num_events = $num_events");
  }

1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:
##   pod2man Net/Telnet.pm | groff -man -Tps > Net::Telnet.ps

=head1 NAME

FTMON::LogFileScraper - 

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
