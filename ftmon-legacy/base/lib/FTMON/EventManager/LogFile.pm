package FTMON::EventManager::LogFile;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: LogFile.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Writes events to a "rollover" logfile as the occur.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager/LogFile.pm,v $
#
#   $Date: 2003/01/10 13:11:01 $
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
#      PO Box 238
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use FTMON::EventManager;
# ----------------------------------------------------------------------
  $DEBUG = 0 if ( ! defined($FTMON::EventManager::LogFile::DEBUG) );

  @FTMON::EventManager::LogFile::ISA = ("FTMON::EventManager");

  $_LAST_ATTRIB = $FTMON::EventManager::_LAST_ATTRIB + 4;
  my( $FILE_NAME,
     $FILE_PATH,
     $FILE_SIZE,
     $NUM_FILES, ) =
    ( $FTMON::EventManager::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB);

  # ----------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto  = shift;

    my $name = shift;
    my $file_name = shift;
    my $file_path = shift;
    my $file_size = shift;
    my $num_files = shift;

    my $class = ref($proto) || $proto;

    my $self = $class->SUPER::new($name);

    bless($self, $class);

    $self->set_attribute($FILE_NAME, $file_name);
    $DEBUG && TraceFuncs::debug("file_name = " . $self->file_name()); 

    $self->set_attribute($FILE_PATH, $file_path);
    $DEBUG && TraceFuncs::debug("file_path = " . $self->file_path()); 

    $self->set_attribute($FILE_SIZE, $file_size);
    
    $self->set_attribute($NUM_FILES, $num_files);

    return($self);
  }

  # ----------------------------------------------------------------------
  sub DESTROY
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self  = shift;
    $self->SUPER::DESTROY();
  }

  # ----------------------------------------------------------------------
  sub dump_html
  {
    local($self, *fh) = @_;
    $self->SUPER::dump_html(*fh);
  }

  sub init 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    $self->SUPER::init()
  }

  # ----------------------------------------------------------------------
  sub send_event 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    my $msg = 
       $event->event_id() . " : " .
       $event->status() .   " : " .
       $event->severity() .   " : " .
       $event->message();

    $DEBUG && TraceFuncs::debug($msg);


    my $log_file = $self->file_path() . "/" . $self->file_name();
    my $old_file;
    my $new_file;

    if ( -e $log_file && 
         ( -s $log_file ) > $self->file_size() )
    {
      $old_file = $log_file . "." . $self->num_files();
      if ( -e $old_file )
      {
        unlink($old_file) || die "Could not remove $old_file - $!";
      }

      for ( $i = ( $self->num_files() - 1 ); $i > 0;  $i-- )
      {
        $old_file = $log_file . "." . $i;
        $new_file = $log_file . "." . ($i + 1);
        if ( -e $old_file )
	{
          rename( $old_file, $new_file ) ||
            die "Could not rename '$old_file' to '$new_file' - $!";
	}
      }
      $new_file = $log_file . ".1";
      rename( $log_file, $new_file ) ||
        die "Could not rename '$log_file' to '$new_file' - $!";
    }
   
    open( LOG, ">>$log_file" ) ||
       die "Could not open $log_file - $!";
  
    my $l_date_str = $FT::Mth . " " .
                     $FT::dd . " " .
  		     $FT::hh . ":" .
		     $FT::mm . ":" .
		     $FT::ss;
     print LOG $l_date_str . " " . $FT::HOSTNAME . " " . $msg . "\n";
     close (LOG);

    return(1);
  }

  # ----------------------------------------------------------------------
  sub next_file 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $current = shift;

    my $log_file;
    if ( ! defined $current )
    {
      $log_file = $self->file_path() . "/" . $self->file_name();
      if ( -e $log_file )
      {
        return $log_file;
      }
      else
      {
        return undef;
      }
    }

    #
    # No more logfiles.
    #
    $log_file = $self->file_path() . "/" . 
                $self->file_name() . "." . $self->num_files();
    return undef if ( $current eq $log_file );

    #
    # Next logfile.
    #
    $log_file = $self->file_path() . "/" . 
                $self->file_name() . ".";
    if ( $current =~ /^${log_file}(\d+)$/ )
    {
      my $next_index = $1 + 1;
      $log_file = $log_file . $next_index;
      return $log_file if ( -e $log_file);
    }
    return undef;

  }
  
  # ----------------------------------------------------------------------
  sub file_name 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    $DEBUG && TraceFuncs::debug($FILE_NAME . ":" . $self->[$FILE_NAME]);
    return($self->[$FILE_NAME]);
  }
  
  # ----------------------------------------------------------------------
  sub file_size 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    $DEBUG && TraceFuncs::debug($self->[$FILE_SIZE]);
    return($self->[$FILE_SIZE]);
  }

  # ----------------------------------------------------------------------
  sub num_files 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return($self->[$NUM_FILES]);
  }
  
  # ----------------------------------------------------------------------
  sub file_path 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return($self->[$FILE_PATH]);
  }

1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::EventManager::LogFile - 

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
