#!/usr/bin/env python

import shlex
import sys
import re
import hashlib
import binascii
import subprocess

import pprint

grub_editenv="grub2-editenv"

functions = {}
variables = {}
# list of commands run
cmd_list = []
menu_list = []

reserved_cmds=set(["insmod", "set", "echo", "linux", "initrd", "menuentry", "submenu", "function", "if", "else", "elif", "fi", "}", "load_env", "save_env", "export", "terminal_output", "search", "source", "load_env"])
measured_cmds=set(["insmod", "set", "echo", "linux", "initrd", "export", "terminal_output", "search", "source", "load_env"])


def get_cmd(line_in):
	"""
	return the command and arguments from the line provided
	"""
	return shlex.split(line_in.strip())

def parse_line(cmd_args, f, cmds):
	"""
	Parse a single line, generating the appropriate hash
	
	NOTE: cmd_args is a the whitespace-separated list from get_cmd
	"""
	
	# ignore an empty line
	if len(cmd_args) == 0 or cmd_args[0].startswith("#"):
		return
	
	if cmd_args[0] not in reserved_cmds and len(cmd_args) == 1:
		# maybe we're in a variable setting...?
		set_args = cmd_args[0].split("=")
		if len(set_args) == 2 and len(set_args[0]) > 0:
			variables[set_args[0]] = set_args[1].strip('"').strip("'")
	
	# let's attept to set any variables we've got...
	d_idx = 0
	for i, c in enumerate(cmd_args):
		final_str = c
		found = False
		
		#print "Looking for dollar in", c
	
		d_idx = c.find('$')
		while d_idx != -1:
			#print "FOUND:", c[d_idx:]
			# OK, we found a dollar sign!
			# make sure it isn't prepended by a backslash
			if len(c) > d_idx+1 and (d_idx == 0 or (d_idx > 0 and c[d_idx-1] != '\\') ):
				unbraced=True
				# time to get the name of the next variable
				if c[d_idx+1] == '{':
					#print "searching for braced variable, starting with", c[d_idx+1:]
					unbraced=False
					b_idx = min(c.find('}',d_idx+1),len(c))
				else:
					#print "searching for end of non-braced variable"
					b_obj = re.search(r'[^0-9A-Za-z_]', c[d_idx+1:])
					b_idx = len(c) if b_obj is None else d_idx+1+b_obj.start()

				var_name = c[(d_idx+2-unbraced):(b_idx)]
				#print "Searching for variable:", var_name
				var_val = variables.get(var_name, "")
				# build my final string
				found = True
				final_str = c[:d_idx] + var_val + c[b_idx+(not unbraced):]
				d_idx += 1
				
			d_idx = final_str.find('$', d_idx)
		
		if found:
			cmd_args[i] = final_str
	# All variables are set now
	
	if cmd_args[0] in measured_cmds:
		cmds.append(' '.join(cmd_args))
	elif cmd_args[0] in functions:
		# put the function command list right in the main command list
		cmds.extend(functions[cmd_args[0]])
		# function commands come AFTER the commands run in the function
		cmds.append(' '.join(cmd_args))
	elif cmd_args[0] == "if" and cmd_args[-1] == "then":
#		print "parsing if"
		cmds.append(parse_if(' '.join(cmd_args[1:-1]), f))
	
	# done parsing this line
	return	
	
def parse_function(fn_name, f):
	"""
	Parse a function definition line and the remainder of the function
	"""
	next_cmd = ""
	
	cmds = []
	while next_cmd != "}":
		cmd_line = f.readline()
		if cmd_line == "":
			raise "ERROR: EOF before '}' detected parsing function; malformed input?"
		
		cmd_args = get_cmd(cmd_line)
		next_cmd = "" if len(cmd_args) == 0 else cmd_args[0]
		#print next_cmd
		#print cmd_args
		if len(cmd_args) > 0:
			next_cmd = cmd_args[0]
			parse_line(cmd_args, f, cmds)
		
	# done parsing function
	functions[fn_name] = cmds	
	
def parse_if(condition, f):
	"""
	Parse an "if/else" block.
	
	The return from this will be a 3-item tuple; the first stating the condition
	itself, the second is a command list for if the condition is true, and the
	third is a command list for if the condition is false.  Note that the command
	lists may themselves contain conditionals
	true
	"""
	next_cmd = ""
	
	t_list = []
	f_list = []
	
	in_true = True;
	# read on until a "fi"
	while next_cmd != "fi":
		cmd_line = f.readline()
		if cmd_line == "":
			raise "ERROR: EOF before 'fi' detected; malformed input?"
		
		cmd_args = get_cmd(cmd_line)
		next_cmd = "" if len(cmd_args) == 0 else cmd_args[0]
		if len(cmd_args) > 0:
			next_cmd = cmd_args[0]
			if cmd_args[0] == "else" or cmd_args[0] == "elif":
				if not in_true:
					raise "ERROR: multiple else?"
				
				in_true = False
				
				# single "fi" on elif condition
				if cmd_args[0] == "elif" and cmd_args[-1] == "then":
					f_list.append(parse_if(' '.join(cmd_args[1:-1]), f))
					break
			else:
				parse_line(cmd_args, f, t_list if in_true else f_list)
	
	# OK, we have a "fi", return our tuple
	#print (condition, t_list, f_list)
	return (condition, t_list, f_list)
	
	
def parse_menuentry(name, f):
	"""
	Parse a menuentry command
	"""
	next_cmd = ""
	cmds = ["setparams " + name]
	
	while next_cmd != "}":
		cmd_line = f.readline()
		if cmd_line == "":
			raise "ERROR: EOF before '}' detected parsing function; malformed input?"
		
		cmd_args = get_cmd(cmd_line)
		if len(cmd_args) > 0:
			next_cmd = cmd_args[0]
			parse_line(cmd_args, f, cmds)
		
	# done parsing function
	menu_list.append(cmds)

	
def parse_submenu(name, f):
	"""
	Parse a submenu command - probably won't be necessary
	"""
	next_cmd = ""
	
	while next_cmd != "}":
		cmd_line = f.readline()
		if cmd_line == "":
			raise "ERROR: EOF before '}' detected parsing function; malformed input?"
		
		parsing_menu=False
		cmd_args = get_cmd(cmd_line)
		if len(cmd_args) > 0 and not cmd_args[0].startswith("#"):
			next_cmd = cmd_args[0]
			if next_cmd == "submenu":
				parse_submenu(cmd_args[1], f)
			elif next_cmd == "menuentry":
				parse_menuentry(cmd_args[1], f)
			elif next_cmd != "}":
				raise "ERROR: Unsupported command in submenu"

def hash_cmd(in_str):
	m = hashlib.sha1()
	m.update(in_str)
	
	#print "Hashing", in_str
	#print m.hexdigest()
	
	return m.hexdigest()
	
def hash_cmd_list(in_list):
	hash_list = []
	
	for c in in_list:
		
		if isinstance(c, tuple):
			# in this case, we have an "if" statement
			hash_list.append( (c[0], hash_cmd_list(c[1]), hash_cmd_list(c[2]) ) )
		else:
			hash_list.append( hash_cmd(c) )
	
	return hash_list
	
def find_tuple_path(cmd_tuple, last_hashes):
	"""
	Find the best path through an if/else block.
	"""
	t_path = find_command_hash(cmd_tuple[1], last_hashes)
	f_path = find_command_hash(cmd_tuple[2], last_hashes)
	
	# I want the priority to be the fewest # of misses, followed by the 
	# most hashes consumed, then true
	sort_list = [(t_path[0], -t_path[1], 0), (f_path[0], -f_path[1], 1)]
	sort_list.sort()
	
	if sort_list[0][2] == 0:
		return t_path
	else:
		return f_path
	
	
def find_command_hash(cmd_hash, last_hashes):
	"""
	Finds the best path through the given commands, and returns a tuple of the 
	number of misses, number of hashes consumed, as well as the best path list
	"""
	curr_idx = 0
	num_misses = 0
	hash_list = []
	
	for c in cmd_hash:
		if isinstance(c, tuple):
			tpath = find_tuple_path(c,last_hashes[curr_idx:])
			num_misses += tpath[0]
			curr_idx += tpath[1]
			hash_list.extend(tpath[2])
		else:
			# here we have a hash
			# we'll definitely hit this hash, so add it!
			hash_list.append(c)

			# search from the current position looking for a matching hash			
			t_idx = curr_idx
			while t_idx < len(last_hashes) and c != last_hashes[t_idx]:
				t_idx += 1
			
			# hash not found, add to num misses
			if t_idx == len(last_hashes):
				num_misses += 1
			# hash found, add to number of consumed
			else:
				curr_idx = t_idx+1
				
	return (num_misses, curr_idx, hash_list)
			
			
	

def find_best_path(cmd_hash, menu_hash, last_hashes):
	"""
	Attempts to heuristically find the best path through both the command hahes
	and the menu hashes to generate a best-fit command path
	
	Will return a list of hashes that should be generated on the next boot
	"""
	# First, hash the commands
	chash = find_command_hash(cmd_hash, last_hashes)

	#print "==== Command Hashes ===="
	#pprint.pprint(cmd_hash)

	#print "==== Consumed Command Hashes ===="
	#pprint.pprint(last_hashes[:chash[1]])

	# next, hash only the first menu entry (we can heuristically find a better 
	# solution a la find_tuple_path, but this is easier and functionally 
	# consistent with PCR 10/14

	#print "==== Menu Commands ===="
	#pprint.pprint(menu_hash[0])

	#print "==== Last Hashes ===="
	#pprint.pprint(last_hashes[chash[1]:])
	
	mhash = find_command_hash(menu_hash[0], last_hashes[chash[1]:])
	
	return chash[2] + mhash[2]
	
	
def chain_hashes(hash_list):
	"""
	Chains the hashes in the method for a TPM chain.  Will return a hex digest
	of the final TPM PCR value to be expected from the list of hashes
	"""
	# start with 20 bytes of 0
	currval = '\0' * 20
	
	for h in hash_list:
		v = currval + binascii.a2b_hex(h)
		m = hashlib.sha1()
		m.update(v)
		currval = m.digest()
	
	return binascii.b2a_hex(currval)
		
if __name__ == "__main__":

	# First, let's set any variables from the grubenv
	grubvars = subprocess.check_output(grub_editenv + " list", shell=True)
	#print "==== Variables ====\n",grubvars
	for v in grubvars.split('\n'):
		varval = v.split('=', 1)
		if len(varval) == 2:
			variables[varval[0]] = varval[1]

	#pprint.pprint(variables)

	in_f = file(sys.argv[1], 'r')
	#hashf_in = file(sys.argv[2], 'r')
	
	next_line = in_f.readline()
	while next_line != "":
		cmd_args = get_cmd(next_line)
		
#		print cmd_args
		
		if len(cmd_args) > 0 and not cmd_args[0].startswith("#"):
			next_cmd = cmd_args[0]
			
			if next_cmd == "function":
				parse_function(cmd_args[1], in_f)
			elif next_cmd == "menuentry":
				parse_menuentry(cmd_args[1], in_f)
			elif next_cmd == "submenu":
				parse_submenu(cmd_args[1], in_f)
			else:
				parse_line(cmd_args, in_f, cmd_list)
				
		next_line = in_f.readline()
		
	# now show me
	#print "=== Command List ==="
	#pprint.pprint(cmd_list)
	
	#print "=== Menu List ==="
	#pprint.pprint(menu_list)
	
	# Now, let's generate some hashing!
	hash_cmds = hash_cmd_list(cmd_list)
	
	
	hash_menu = []
	for l in menu_list:
		hash_menu.append(hash_cmd_list(l))
	
	#print "=== Hashed Commands ==="
	#pprint.pprint(hash_cmds)
	
	#print "=== Hashed Menu ==="
	#pprint.pprint(hash_menu)
	
	last_hashes=[]
	with file(sys.argv[2], 'r') as f:
		for l in f:
			last_hashes.append(l.strip())
		
	#print "=== Last hashes ==="
	#pprint.pprint(last_hashes)
	
	next_boot_hash = find_best_path(hash_cmds, hash_menu, last_hashes)
	
	# Now, chain all the hashes in the next boot hash, starting with all 0
	print chain_hashes(next_boot_hash)
	
	#pprint functions
	#pprint variables

		
	
