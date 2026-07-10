package FTMON::Environment;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Environment.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Defines OS specific attributes.
#   @(#) REVISIT:
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Environment.pm,v $
#
#   $Date: 2003/01/10 13:10:54 $
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

use FTMON::Base;
use FTMON::Helper;

#
# Provides a "best effort" determination of the environment"
# Each operating system that becomes supported will need to derive their
# own environment from this class and override $Environment::SINGLETON
#

$DEBUG = 0 if ( ! defined($FTMON::Environment::DEBUG) );

@FTMON::Environment::ISA = ("FTMON::Base");

my $HTML_FILE = "info.html";

($MODE_MONITOR,
 $MODE_TEST,
 $MODE_CHECK,
 $MODE_CRYPT,
 $MODE_INFO,
 $MODE_UNLOCK,
 $MODE_RESET_CFG,
 $MODE_MERGE,
 $MODE_UNMERGE,
 $MODE_REBUILD,
 $MODE_UNREBUILD ) = ( 0 .. 10 );

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 7;
my($MODE,
   $HOSTNAME,
   $OS_NAME,
   $OS_VERSION,
   $BASE_DIR,
   $CONFIG_FILE,
   $PRODUCTS,
   ) = ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# --------------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $proto = shift;

  my $class = ref($proto) || $proto;
  my $self = $class->SUPER::new("Environment");
 
  $self->[$MODE] = undef;

  #REVISIT
  $self->[$OS_VERSION] = undef;

  $self->[$OS_NAME]  = $^O;

  bless($self, $class);

  $self->[$PRODUCTS]  = {};

  #
  # ftmon assumes that the perl packages are in the same directory as this 
  # script, so setup the package path accordingly.
  #
  $FT::SCRIPT_PATH = $0;
  $FT::SCRIPT_PATH =~ s/\\/\//g;
  $FT::SCRIPT = substr($FT::SCRIPT_PATH, rindex($FT::SCRIPT_PATH, "/") + 1);
  $FT::SCRIPT_PATH =~ s/$FT::SCRIPT//;
  $FT::SCRIPT      =~ s/\.pl//;
  $FT::SCRIPT      =~ s/\.exe//;

  if ( ! $FT::SCRIPT_PATH )
  {
    die "You must run $FT::SCRIPT with an absolute path.";
  }

  $self->base_directory($FT::BASE_DIR);
  $self->html_dir();
  $self->log_dir();

  #
  # REVISIT: hostname should exist on all the supported architectures.
  #
  $FT::HOSTNAME = `hostname`;
  chomp($FT::HOSTNAME);
  $FT::HOSTNAME =~ tr/A-Z/a-z/;
  $self->hostname($FT::HOSTNAME);

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}

# --------------------------------------------------------------------------
# This is for default purposes only. It should be over-ridden with the
# OS specific environment.
$SINGLETON = FTMON::Environment->new();


# --------------------------------------------------------------------------
sub mode
{
  my $self = shift;

  if (@_) 
  {
    $self->[$MODE] = shift;
  }

  return($self->[$MODE]);
}

# --------------------------------------------------------------------------
sub hostname
{
  my $self = shift;

  if (@_) 
  {
    $self->[$HOSTNAME] = shift;
    $FT::HOSTNAME = $self->[$HOSTNAME];
  }

  return($self->[$HOSTNAME]);
}

# ----------------------------------------------------------------------
sub add_product
{
  my $self = shift;
  my $product = shift;
  $self->[$PRODUCTS]->{$product->name()} = $product;
}

# ----------------------------------------------------------------------
sub find_product
{
  my $self = shift;
  my $product_name = shift;
  if ( exists $self->[$PRODUCTS]->{$product_name} )
  {
    return $self->[$PRODUCTS]->{$product_name};
  }
  else
  {
    return undef;
  }
}


# --------------------------------------------------------------------------
sub os_name
{
  my $self = shift;

  if (@_) 
  {
    $self->[$OS_NAME] = shift;
  }

  return($self->[$OS_NAME]);
}

# --------------------------------------------------------------------------
sub os_version
{
  my $self = shift;

  if (@_) 
  {
    $self->[$OS_VERSION] = shift;
  }

  return($self->[$OS_VERSION]);
}

# --------------------------------------------------------------------------
sub base_directory
{
  my $self = shift;

  if (@_) 
  {
    $self->[$BASE_DIR] = shift;
  }

  return($self->[$BASE_DIR]);
}

# --------------------------------------------------------------------------
sub html_dir
{
  my $self = shift;

  my $html_dir = $self->base_directory() . "/html";
  if ( defined($FT::HTML_DIR) && $FT::HTML_DIR )
  {
    $html_dir = $FT::HTML_DIR;
  }


  return($html_dir);
}

# --------------------------------------------------------------------------
sub cfg_dir
{
  my $self = shift;

  my $cfg_dir = $self->base_directory() . "/cfg";
  if ( defined($FT::CFG_DIR) && $FT::CFG_DIR )
  {
    $cfg_dir = $FT::CFG_DIR;
  }

  return($cfg_dir);
}

# --------------------------------------------------------------------------
sub log_dir
{
  my $self = shift;

  my $log_dir = $self->base_directory() . "/logs";
  if ( defined($FT::LOG_DIR) && $FT::LOG_DIR )
  {
    $log_dir = $FT::LOG_DIR;
  }

  return($log_dir);
}

# ----------------------------------------------------------------------
# post-condition:
#	- open jobs sorted and dumped to html file
sub dump_html
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my(@col_names) = ("Product", "Summary", "Owner", "Trading", "Severity" );
  my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
                  "index.html";
  open(HTML,  "> $html_path") || 
    die "Could not open $html_path - $!";


  FTMON::Helper::http_page_begin(
     *HTML,
     "products", 
     60,
     "<P><b>Description:</b> The following is a summary of the status of all the products currently being monitored by FTMON. You can drill down for more information on the status of each Product.</P>",
     "./");

  FTMON::Helper::http_table_start(*HTML, "", \@col_names);

    
  $fmt_str = 
	      '<TR>' .
	      '<TD><LEFT><FONT SIZE="-1">%s</FONT></LEFT></TD>' .
	      '<TD><LEFT><FONT SIZE="-1">%s</FONT></LEFT></TD>' .
	      '<TD><LEFT><FONT SIZE="-1">%s</FONT></LEFT></TD>' .
	      '<TD><LEFT><FONT SIZE="-1">%s</FONT></LEFT></TD>' .
	      '<TD BGCOLOR="%s"><LEFT>' .
	      '<FONT COLOR="%s" SIZE="-1">%s</FONT></LEFT></TD></TR>';


  # my(@col_names) = ("Package", "Description", "Owner", "Status" );
  my @product_details = ();
  my $fg_color = "white";
  my $bg_color = "black";
  my $severity;
  my $product_html;
  my $product_name;

  foreach $product ( values %{$self->[$PRODUCTS]} )
  {
    $product_name = $product->name();
    $html_path = $product->name() .
                  "/index.html";
    $html_path =~ s/::/\//g;

    $severity = $product->severity(),
    $fg_color = "white";
    $bg_color = "black";
    $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
		         if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
    $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
		         if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );


    $product_html = "<a href=\"$html_path\">" . $product->name() . "</a>";
    $product->dump_html();

    push(@product_details,
	  [ $product_html,
	    $product->summary(),
	    $product->contact(),
	    $FT::TRADING_MSG{$product_name},
	    $bg_color, $fg_color, $severity
	  ] );

  }
    
  FTMON::Helper::print_table(*HTML, \@product_details, '-1', $fmt_str);
  FTMON::Helper::http_table_end(*HTML);

  FTMON::Helper::http_page_end(*HTML);

  close(HTML) ||
      die "Could not write to $html_path - $!";

  return 0;

}


sub lock_test
{
}

sub is_lock
{
}

sub unlock
{
}


1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::Environment - 

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
