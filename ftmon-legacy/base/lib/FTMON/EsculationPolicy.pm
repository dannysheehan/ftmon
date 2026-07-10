package FTMON::EsculationPolicy;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: EsculationPolicy.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Specification of policies to adopt at different event repeat counts.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EsculationPolicy.pm,v $
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
use TraceFuncs;
# ----------------------------------------------------------------------
$DEBUG = 0 if ( ! defined($FTMON::EsculationPolicy::DEBUG) );

#
# CLASS Varaibles

$_LAST_ATTRIB = 1;
my(
   $POLICY,
   ) = ( 0  );

# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  $DEBUG && TraceFuncs::trace(my $f);

  my $policy = shift;

  my $class = ref($proto) || $proto;
  
  my $self = [];
  bless($self, $class);
 
  $self->policy($policy);
  $self->check();

  return($self);
}


# ----------------------------------------------------------------------
sub cmp
{
  my $self = shift;
  my $other = shift;

  my $self_policy = $self->policy();
  my $other_policy = $other->policy();
  return 0 if ( @{$self_policy} != @{$other_policy} );

  for ( $i = 0; $i < @{$self_policy}; $i++ )
  {
    return 0 if ( $self_policy->[$i] ne $other_policy->[$i] );
  }

  return(1);
}



# ----------------------------------------------------------------------
sub check
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  my $policy = $self->policy();
  die "$policy - '" . ref($policy) . "' is not an array reference" 
     if ( ref($policy) ne "ARRAY" );
  $self->dump();

  my $size = @{$policy};
  my @new_policy = ();
#    if (ref($policy) ne "ARRAY")
#    {
#      @new_policy = ( 0, $policy );
#    }
#    elsif ( $size % 2 )
#    {
#      @new_policy = ( 0, @{$policy} );
#    }
#    $policy = \@new_policy;


  my $repeat_count = 0;
  my $policy_entry;
  my $prev_repeat_count = -99999999;
  for ( $i = 0; $i < @{$policy}; $i += 2 )
  {
    $repeat_count = $policy->[$i];
    $policy_entry = $policy->[$i + 1];
    if ( $repeat_count !~ /\d+/ )
    {
      die "Invalid policy: [$repeat_count, $policy_entry] '$repeat_count' " .
	    "repeat count entries must be numeric " .
	    "and non negative";
    }

    if ( $repeat_count <= $prev_repeat_count )
    {
      die "Repeat counts must increase - " .
	    $repeat_count . " must be > " . $prev_repeat_count;
    }

    $prev_repeat_count = $repeat_count;

  }
}

# ----------------------------------------------------------------------
sub current_policy
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $repeat_count = shift;

  my $found = 0 ;
  my $current_policy = undef;
  my $i;
  my $current_repeat_count = 0;

  die "undefined Policy object" if ( ! defined $self );
  my $policy = $self->policy();

  die "esculation policy is invalid" if ( @{$policy} - 2 < 0 );

  for ( $i = ( @{$policy} - 2 ); $i >= 0;  $i -= 2 )
  {
    die "POLICY: " . $self->dump() . " Not defined at repeat_count $i"
      if ( ! defined $policy->[$i] );

    $current_repeat_count = $policy->[$i];
    $DEBUG && TraceFuncs::debug(
       $i . " = " . $current_repeat_count . ", " . 
	              $policy->[$i + 1] );

    if ( $repeat_count >= $current_repeat_count )
    {
      $found = $i / 2 + 1 ;
      $DEBUG && TraceFuncs::debug("found = $found");

	$found = 1 if ($found < 1);
	$current_policy = $policy->[$found];
	last;
    }
  }

  if ( wantarray )
  {
    $DEBUG && TraceFuncs::debug(
	         "current = $current_policy, $current_repeat_count");
    return($current_policy, $current_repeat_count);
  }
  else
  {
    $DEBUG && TraceFuncs::debug("current = $current_policy");
    return($current_policy);
  }
}

# ----------------------------------------------------------------------
sub dump
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);

  my $str = "";
  my $policy = $self->policy();
  for ( $i = ( @{$policy} - 2 ); $i >= 0;  $i -= 2 )
  {
    $str = $str .
       $i . " : " . $policy->[$i] . ", " . 
	              $policy->[$i + 1] . "\n";
  }

  my $size = @{$policy};
  $DEBUG && TraceFuncs::debug("size=" . $size . "\n" . $str);
  return($str);
}

# ----------------------------------------------------------------------
sub str
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $str = "";

  my $policy = $self->policy();

  for ( $i = ( @{$policy} - 2 ); $i >= 0;  $i -= 2 )
  {
    $str = $str .
       $i . " : " . $policy->[$i] . ", " . 
	              $policy->[$i + 1];
  }

  return $str;
}

# ----------------------------------------------------------------------
sub dump_html
{
  my $self = shift;
  my $size = shift;

  $DEBUG && TraceFuncs::trace(my $f);

  $size = "+0" if ( ! defined $size );

  my $policy = $self->policy();

  my $html_str = "\n      <TABLE>\n";
  
  for ( $i = ( @{$policy} - 2 ); $i >= 0;  $i -= 2 )
  {
    $html_str = $html_str . "        <TR>\n" .
       "          <TD><FONT SIZE=$size><b>count=</b>" . 
	               $policy->[$i] . "</FONT></TD>\n" . 
       "          <TD><FONT SIZE=$size>" .
	               $policy->[$i + 1] . "</FONT></TD>\n" .
	 "      </TR>\n";
  }
  $html_str = $html_str . "      </TABLE>\n";

  return $html_str;
}

# ----------------------------------------------------------------------
# policy
# - check specified policy before setting it as the current policy
# NB This subroutine will die if the policy is not valid.
sub policy
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  if (@_) 
  {
    $self->[$POLICY] = shift;
  }
  return($self->[$POLICY]);
}


# ----------------------------------------------------------------------

{
package FTMON::MessageEsculationPolicy;
$DEBUG = 0 if ( ! defined($FTMON::MessageEsculationPolicy::DEBUG) );  
@FTMON::MessageEsculationPolicy::ISA = ("FTMON::EsculationPolicy");


# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $policy = shift;

  my $class = ref($proto) || $proto;
  my $self = [];

  $self = $class->SUPER::new($policy);
  bless($self, $class);
  

  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}

# ----------------------------------------------------------------------
#  sub check
#  {
#    $DEBUG && TraceFuncs::trace(my $f);
#    my $self = shift;
#    $self->dump();
#    $self->SUPER::check();
#
#  }

};


# ----------------------------------------------------------------------
{
package FTMON::SeverityEsculationPolicy;
$DEBUG = 0 if ( ! defined($FTMON::SeverityEsculationPolicy::DEBUG) );  

@FTMON::SeverityEsculationPolicy::ISA = ("FTMON::EsculationPolicy");

# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $policy = shift;

  my $class = ref($proto) || $proto;

  my $self = $class->SUPER::new($policy);

  bless($self, $class);

  return($self);
}
# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}


# ----------------------------------------------------------------------
#  sub check
#  {
#    $DEBUG && TraceFuncs::trace(my $f);
#    my $self = shift;
#    $self->dump();
#    $self->SUPER::check();
#  }

# ----------------------------------------------------------------------
sub dump_html
{
  my $self = shift;
  my $size = shift;

  $DEBUG && TraceFuncs::trace(my $f);

  $size = "+0" if ( ! defined $size );

  my $policy = $self->policy();

  my $html_str = "\n      <TABLE>\n";
  
  for ( $i = ( @{$policy} - 2 ); $i >= 0;  $i -= 2 )
  {
    my $fg_color = "white";
    my $bg_color = "black";
    $severity = $policy->[$i+1];
    $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
        if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
    $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
        if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );
    $html_str = $html_str . "        <TR>\n" .
       "          <TD><FONT SIZE=$size><b>count=</b>" . 
	               $policy->[$i] . "</FONT></TD>\n" . 
       "          <TD BGCOLOR=\"$bg_color\">" .
	            "<FONT COLOR=\"$fg_color\" SIZE=$size>" .
                   $severity . "</FONT></TD>\n" . 
	 "      </TR>\n";
  }
  $html_str = $html_str . "      </TABLE>\n";

  return $html_str;
}

};

# ----------------------------------------------------------------------

{
package FTMON::RetryEsculationPolicy;
$DEBUG = 0 if ( ! defined($FTMON::RetryEsculationPolicy::DEBUG) );

@FTMON::RetryEsculationPolicy::ISA = ("FTMON::EsculationPolicy");

# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $policy = shift;

  my $class = ref($proto) || $proto;

  my $self = $class->SUPER::new($policy);

  bless($self, $class);


  return($self);
}
# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}


# ----------------------------------------------------------------------
sub dump
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);

  my $str = ""; 
  my $policy = $self->policy();

  for ( $i = ( @{$policy} - 3 ); $i >= 0;  $i -= 3 )
  {
    $str = $str .
       $i . " : " . $policy->[$i] . ", " . 
	              $policy->[$i + 1] . ", " .
	              $policy->[$i + 2] . "\n";
  }

  my $size = @{$policy};
  $DEBUG && TraceFuncs::debug("||" . $size . ":" . $str);
  return $str;
}

# ----------------------------------------------------------------------
sub dump_html
{
  my $self = shift;
  my $size = shift;

  $DEBUG && TraceFuncs::trace(my $f);

  $size = "+0" if ( ! defined $size );
  my $policy = $self->policy();

  my $html_str = "\n      <TABLE>\n";

  for ( $i = ( @{$policy} - 3 ); $i >= 0;  $i -= 3 )
  {
    my $retries = ( $policy->[$i + 1] == -1) 
                     ? "forever" : $policy->[$i + 1];
                   
    $html_str = $html_str . "        <TR>\n" .
       "          <TD><FONT size=$size><b>count=</b>" . 
	              $policy->[$i] . "</FONT></TD>\n" . 
	 "          <TD><FONT size=$size><b>retry=</b>" . 
	              $retries . "</FONT></TD>\n" .
       "          <TD><FONT SIZE=$size>" .
	              $policy->[$i + 2] . "</FONT></TD>\n" .
	 "      </TR>\n";
  }
  $html_str = $html_str . "      </TABLE>\n";

  return $html_str;
}


# ----------------------------------------------------------------------
sub check
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;


  my $policy = $self->policy();
  die "'" . ref($policy) . "' is not an array reference" 
     if ( ref($policy) ne "ARRAY" );
  $self->dump();

  my $size = @{$policy};
#    my @new_policy =  @{$policy};
#    if (ref($policy) ne "ARRAY")
#    {
#      @new_policy = ( 0, 1, $policy );
#    }
#    elsif ( ( $size % 3 ) == 1 )
#    {
#      @new_policy = ( 0, 1, @{$policy} );
#    }
#    elsif ( ( $size % 3 ) == 2 )
#    {
#      @new_policy = ( 0, @{$policy} );
#    }
#    $policy = \@new_policy;

  my $repeat_count = 0;
  my $policy_entry;
  my $prev_repeat_count = -99999999;
  for ( $i = 0; $i < @{$policy}; $i += 3 )
  {
    $repeat_count = $policy->[$i];
    $retries      = $policy->[$i + 1];
    $policy_entry = $policy->[$i + 2];
    if ( $repeat_count !~ /\d+/ )
    {
      die "Invalid policy: " .
	    "[$repeat_count, $retries, $policy_entry] '$repeat_count' " .
	    "repeat count entries must be numeric " .
	    "and non negative";
    }
    if ( $retries !~ /\-?\d+/ )
    {
      die "Invalid policy: '$retries' " .
	    "retries entries must be numeric ";
    }

    if ( $repeat_count <= $prev_repeat_count )
    {
      die "Repeat counts must increase - " .
	    $repeat_count . " must be > " . $prev_repeat_count;
    }

    $prev_repeat_count = $repeat_count;

  }
}

# ----------------------------------------------------------------------
sub current_policy
{
  my $self = shift;
  $DEBUG && TraceFuncs::trace(my $f);
  my $repeat_count = shift;

  my $found = 0 ;
  my $current_policy = undef;
  my $i;
  my $current_repeat_count = 0;
  my $current_retries      = 0;

  my $policy = $self->policy();

  die "esculation policy is invalid" if ( @{$policy} - 3 < 0 );

  for ( $i = ( @{$policy} - 3 ); $i >= 0;  $i -= 3 )
  {
    $current_repeat_count = $policy->[$i];
    $current_retries      = $policy->[$i + 1];
    $DEBUG && TraceFuncs::debug(
       $i . " = " . $current_repeat_count . ", " . 
	              $policy->[$i + 2] );

    if ( $repeat_count >= $current_repeat_count )
    {
      $found = $i / 3 + 2 ;
      $DEBUG && TraceFuncs::debug("found = $found");

	$found = 2 if ($found < 2);
	$current_policy = $policy->[$found];
	last;
    }
  }

  if ( wantarray )
  {
    return($current_policy, $current_repeat_count, $current_retries);
  }
  else
  {
    return($current_policy);
  }
}
};

# ----------------------------------------------------------------------
{
package FTMON::ActionEsculationPolicy;
$DEBUG = 0 if ( ! defined($FTMON::ActionEsculationPolicy::DEBUG) );

@FTMON::ActionEsculationPolicy::ISA = ("FTMON::RetryEsculationPolicy");

my $VariableName = "ACTION";

# ----------------------------------------------------------------------
sub new
{
  my $proto  = shift;
  my $policy = shift;
  $DEBUG && TraceFuncs::trace(my $f);

  my $class = ref($proto) || $proto;

  my $self = $class->SUPER::new($policy);

  bless($self, $class);

  return($self);
}
# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}

  # ----------------------------------------------------------------------

#  sub check
#  {
#    $DEBUG && TraceFuncs::trace(my $f);
#    my $self = shift;
#    $self->dump();
#    $self->SUPER::check();
#  }
};

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:
##   pod2man Net/Telnet.pm | groff -man -Tps > Net::Telnet.ps

=head1 NAME

FTMON::EsculationPolicy - 

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
