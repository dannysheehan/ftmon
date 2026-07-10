################################################################################
#	Crypt::CipherSaber
#
#		an object oriented module implementing CipherSaber-1 and CS-2
#		encryption
#
#	copyright (c) 2000 chromatic.  All rights reserved.
#	This program is free software; you can distribute and modify it under the
#	same terms as Perl itself.
################################################################################

package Crypt::CipherSaber;

use strict;
use vars qw($VERSION);

$VERSION = '0.50';

sub new {
	my $class = shift;
	my $key = shift;

	# CS-2 shuffles the state array N times, CS-1 once
	my $N = shift;
	if (!(defined $N) or ($N < 1)) {
		$N = 1;
	}
	my $self = [ $key, [ 0 .. 255 ], $N ];
	bless($self, $class);
}

sub crypt {
	my $self = shift;
	my $iv = shift;
	$self->_setup_key($iv);
	my $message = shift;
	my $state = $self->[1];
	my ($i, $j, $n) = (0, 0, 0);
	my $output;
	for (0 .. (length($message) -1 )) {
		$i++;
		$i %= 256;
		$j += $state->[$i];
		$j %= 256;
		@$state[$i, $j] = @$state[$j, $i];
		$n = $state->[$i] + $state->[$j];
		$n %= 256;
		$output .= chr( $state->[$n] ^ ord(substr($message, $_, 1)) );
	}
	$self->[1] = [ 0 .. 255 ];
	return $output;
}

sub encrypt {
	my $self = shift;
	my $iv = $self->_gen_iv();
	return $iv . $self->crypt($iv, @_);
}

sub decrypt {
	my $self = shift;
	my $message = shift;
	my $iv = substr($message, 0, 10, '');
	return $self->crypt($iv, $message);
}

###################
#
# PRIVATE METHODS
#
###################
sub _gen_iv {
	my $iv;
	$iv .= chr(int(rand(255))) for (1 .. 10);
	return $iv;
}

sub _setup_key {
	my $self = shift;
	my $key = $self->[0] . shift;
	my @key = map { ord } split(//, $key);
	my $state = $self->[1];
	my $j = 0;
	my $length = @key;

	# repeat N times, for CS-2
	for (1 .. $self->[2]) {
		for my $i (0 .. 255) {
			$j += ($state->[$i] + ($key[$i % $length]));
			$j %= 256;
			(@$state[$i, $j]) = (@$state[$j, $i]);
		}
	}
}

1;
