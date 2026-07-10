package FTMON::Product;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Product.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) A Product is a collection of Monitors for a device or application.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Product.pm,v $
#
#   $Date: 2003/01/10 13:11:00 $
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
use FTMON::Base;

  $DEBUG = 0 if ( ! defined($FTMON::Product::DEBUG) );
  @FTMON::Product::ISA = ("FTMON::Base");


  #
  # REVISIT: Consider putting a limit on number of events e.g. on catostophic
  # failure there may be so many events that all memory is used by hash.
 
  #   $THRESHOLD,
  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 5;
  my($NAME,
     $MONITORS,
     $SUMMARY,
     $CONTACT,
     $DESCRIPTION,
     ) = 
        ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );
  
  # ----------------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
  
    my $proto  = shift;

    my $name = shift;
    my $description = shift;
    my $summary = shift;
    my $contact = shift;

    my $class = ref($proto) || $proto;
    my $self = $class->SUPER::new($name);
    
    $self->[$MONITORS] = {};
    $self->[$NAME] = $name;

    $self->[$DESCRIPTION] = "-";
    $self->[$DESCRIPTION] = $description if ( defined $description );

    $self->[$SUMMARY] =  "-";
    $self->[$SUMMARY] = $summary if ( defined $summary ); 

    $self->[$CONTACT] =  "-";
    $self->[$CONTACT] = $contact if ( defined $contact );

    bless($self, $class);

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
  sub name
  {
    my $self = shift;
    if (@_) 
    {
      my $name = shift;
      $self->[$NAME] = $name;
    }
    return($self->[$NAME]);
  }

  # ----------------------------------------------------------------------
  sub severity
  {
    my $self = shift;

    my $monitor;
    my $severity = $FT::SEV[0];
    foreach $monitor (values %{$self->[$MONITORS]})
    {
      $severity = $monitor->severity()
	       if ( defined $monitor->severity() &&
	            FT::ordered_sev($severity) < 
	            FT::ordered_sev($monitor->severity()) );

    }

    return $severity;
  }


  # ----------------------------------------------------------------------
  sub description
  {
    my $self = shift;
    return($self->[$DESCRIPTION]);
  }

  # ----------------------------------------------------------------------
  sub summary
  {
    my $self = shift;
    return($self->[$SUMMARY]);
  }

  # ----------------------------------------------------------------------
  sub contact
  {
    my $self = shift;
    return($self->[$CONTACT]);
  }

  # ----------------------------------------------------------------------
  sub add_monitor
  {
    my $self = shift;
    my $monitor = shift;

    my $monitor_name = $monitor->name();
    $self->[$MONITORS]->{$monitor_name} = $monitor;
  }

  # ----------------------------------------------------------------------
  # post-condition:
  #	- open jobs sorted and dumped to html file
  sub dump_html
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;

    my(@col_names) = ("Monitor", "Summary", "Severity" );

    my $vendor;
    my $product;
    my $name = $self->name();
    ($vendor, $product) = split("::", $name);
    my $html_path = 
       $FTMON::Environment::SINGLETON->html_dir() . "/"  .  $vendor;
    if ( ! -d $html_path )
    {
      mkdir($html_path, 0755) || die "Can not make $html_path";
    }

    $html_path = $html_path . "/" . $product;
    if ( ! -d $html_path )
    {
      mkdir($html_path, 0755) || die "Can not make $html_path";
    }

    $FT::TRADING_MSG{$name} = "N/A" if ( ! defined $FT::TRADING_MSG{$name} );

    open(HTML,  "> $html_path/index.html") || 
      die "Could not open $html_path/index.html - $!";


    FTMON::Helper::http_page_begin(
       *HTML,
       $self->name(),
       60,
       "<b>Summary:</b> " . $self->summary() . "<br>" .
       "<b>Description:</b> " . $self->description() . "<br>" .
       "<b>Contact:</b> " . $self->contact() . "<br>" .
       "<b>Trading:</b> " . $FT::TRADING_MSG{$name},
       "../../");

    FTMON::Helper::http_table_start(*HTML, "", \@col_names);

      
    #my(@col_names) = ("Monitor", "Summary", "Severity" );
    $fmt_str = 
	      '<TR>' .
	      '<TD><FONT SIZE="-1"><LEFT>%s</FONT></LEFT></TD>' .
	      '<TD><FONT SIZE="-1"><LEFT>%s</FONT></LEFT></TD>' .
	      '<TD BGCOLOR="%s"><LEFT>' .
	      '<FONT SIZE="-1" COLOR="%s">%s</FONT></LEFT></TD></TR>';



    my @monitor_details = ();
    my $fg_color = "white";
    my $bg_color = "black";
    my $severity;
    my $monitor_html;
    foreach $monitor (values %{$self->[$MONITORS]})
    {
      $severity = ( defined $monitor->severity() )
                        ? $monitor->severity()
			: $FT::SEV[0];

      $fg_color = "white";
      $bg_color = "black";
      $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
		         if ( defined($FT::SEVERITY_FG_COLOR{$severity}) );
      $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
		         if ( defined($FT::SEVERITY_BG_COLOR{$severity}) );


      my $monitor_path = $monitor->monitor_name() . ".html";

      $monitor_html = "<a href=\"$monitor_path\">" . $monitor->name() . "</a>";
      $monitor_path = $html_path . "/" . $monitor_path;
      if ( ! -f $monitor_path )
      {
        open( MON_FP, "> $monitor_path" ) || die "$monitor_path";
        FTMON::Helper::http_page_begin(
           *MON_FP,  $monitor->name(), 60, 
	   "Monitor may have errors or still be calculating. " .
	   "See Jobs tab for any errors.", 
	   "../../");
        FTMON::Helper::http_page_end(*MON_FP);
	close(MON_FP);
      }

      push(@monitor_details,
	  [ $monitor_html,
	    $monitor->description(),
	    $bg_color, $fg_color, $severity
	  ] );

    }
      
    FTMON::Helper::print_table(*HTML, \@monitor_details, '-1', $fmt_str);
    FTMON::Helper::http_table_end(*HTML);

    FTMON::Helper::http_page_end(*HTML);

    close(HTML);

    return 0;

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

FTMON::Product - 

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
