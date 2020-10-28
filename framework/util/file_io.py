"""Common functions and classes used in multiple places in the MDTF code.
Specifically, util.py implements general functionality that's not MDTF-specific.
"""
import os
import io
import collections
import glob
import json
import logging
import re
import string
import shutil
from . import funcs
from . import exceptions as exc

_log = logging.getLogger(__name__)

def strip_comments(str_, delimiter=None):
    # would be better to use shlex, but that doesn't support multi-character
    # comment delimiters like '//'
    if not delimiter:
        return str_
    s = str_.splitlines()
    for i in list(range(len(s))):
        if s[i].startswith(delimiter):
            s[i] = ''
            continue
        # If delimiter appears quoted in a string, don't want to treat it as
        # a comment. So for each occurrence of delimiter, count number of 
        # "s to its left and only truncate when that's an even number.
        # TODO: handle ' as well as ", for non-JSON applications
        s_parts = s[i].split(delimiter)
        s_counts = [ss.count('"') for ss in s_parts]
        j = 1
        while sum(s_counts[:j]) % 2 != 0:
            j += 1
        s[i] = delimiter.join(s_parts[:j])
    # join lines, stripping blank lines
    return '\n'.join([ss for ss in s if (ss and not ss.isspace())])

def read_json(file_path):
    if not os.path.exists(file_path):
        raise exc.MDTFFileNotFoundError(file_path)
    try:    
        with io.open(file_path, 'r', encoding='utf-8') as file_:
            str_ = file_.read()
    except IOError:
        _log.exception('Fatal IOError when reading %s. Exiting.', file_path)
        exit(1)
    try:
        str_ = parse_json(str_)
    except (UnicodeDecodeError, json.decoder.JSONDecodeError):
        _log.exception('JSON formatting error in file %s', file_path)
        exit(1)
    return str_

def parse_json(str_):
    str_ = strip_comments(str_, delimiter= '//') # JSONC quasi-standard
    try:
        parsed_json = json.loads(str_, object_pairs_hook=collections.OrderedDict)
    except UnicodeDecodeError:
        _log.critical('Unicode error while deocding %s. Exiting.', str_)
        raise
    except json.decoder.JSONDecodeError:
        _log.exception('JSON formatting error.')
        raise
    return parsed_json

def write_json(struct, file_path, sort_keys=False):
    """Wrapping file I/O simplifies unit testing.

    Args:
        struct (:py:obj:`dict`)
        file_path (:py:obj:`str`): path of the JSON file to write.
    """
    try:
        str_ = json.dumps(struct, 
            sort_keys=sort_keys, indent=2, separators=(',', ': '))
        with io.open(file_path, 'w', encoding='utf-8') as file_:
            file_.write(str_.encode(encoding='utf-8', errors='strict'))
    except IOError:
        _log.critical('Fatal IOError when trying to write %s.', file_path)
        exit()

def pretty_print_json(struct, sort_keys=False):
    """Pseudo-YAML output for human-readable debugging output only - 
    not valid JSON"""
    str_ = json.dumps(struct, sort_keys=sort_keys, indent=2)
    for char in ['"', ',', '}', '[', ']']:
        str_ = str_.replace(char, '')
    str_ = re.sub(r"{\s+", "- ", str_)
    # remove lines containing only whitespace
    return os.linesep.join([s for s in str_.splitlines() if s.strip()]) 

def find_files(src_dirs, filename_globs, n_files=None):
    """Return list of files in ``src_dirs``, or any subdirectories, matching any
    of ``filename_globs``. Wraps :py:class:`glob.glob`.

    Args:
        src_dirs: Directory, or a list of directories, to search for files in.
            The function will also search all subdirectories.
        filename_globs: Glob, or a list of globs, for filenames to match. This 
            is a shell globbing pattern, not a full regex.
        n_files (int, optional): If supplied, raise 
            :class:`~framework.util.exceptions.MDTFFileNotFoundError` if the 
            number of files found is not equal to this number.

    Returns: :py:obj:`list` of paths to files matching any of the criteria.
        If no files are found, the list is empty.
    """
    src_dirs = funcs.to_iter(src_dirs)
    filename_globs = funcs.to_iter(filename_globs)
    files = set([])
    for d in src_dirs:
        for g in filename_globs:
            files.update(glob.glob(os.path.join(d, g)))
            files.update(glob.glob(os.path.join(d, '**', g), recursive=True))
    if n_files is not None and len(files) != n_files:
        _log.debug('Expected to find %d files, instead found %d.', n_files, len(files))
        raise exc.MDTFFileNotFoundError(str(filename_globs))
    return list(files)

def recursive_copy(src_files, src_root, dest_root, copy_function=None, 
    overwrite=False):
    """Copy src_files to dest_root, preserving relative subdirectory structure.

    Copies a subset of files in a directory subtree rooted at src_root to an
    identical subtree structure rooted at dest_root, creating any subdirectories
    as needed. For example, `recursive_copy('/A/B/C.txt', '/A', '/D')` will 
    first create the destination subdirectory `/D/B` and copy '/A/B/C.txt` to 
    `/D/B/C.txt`.

    Args:
        src_files: Absolute path, or list of absolute paths, to files to copy.
        src_root: Root subtree of all files in src_files. Raises a ValueError
            if all files in src_files are not contained in the src_root directory.
        dest_root: Destination directory in which to create the copied subtree.
        copy_function: Function to use to copy individual files. Must take two 
            arguments, the source and destination paths, respectively. Defaults 
            to :py:meth:`shutil.copy2`.
        overwrite: Boolean, deafult False. If False, raise an OSError if
            any destination files already exist, otherwise silently overwrite.
    """
    if copy_function is None:
        copy_function = shutil.copy2
    src_files = funcs.to_iter(src_files)
    for f in src_files:
        if not f.startswith(src_root):
            raise ValueError('{} not a sub-path of {}'.format(f, src_root))
    dest_files = [
        os.path.join(dest_root, os.path.relpath(f, start=src_root)) \
        for f in src_files
    ]
    for f in dest_files:
        if not overwrite and os.path.exists(f):
            raise exc.MDTFFileExistsError(f)
        os.makedirs(os.path.normpath(os.path.dirname(f)), exist_ok=True)
    for mdtf, dest in zip(src_files, dest_files):
        copy_function(mdtf, dest)

def resolve_path(path, root_path="", env=None):
    """Abbreviation to resolve relative paths.

    Args:
        path (:obj:`str`): path to resolve.
        root_path (:obj:`str`, optional): root path to resolve `path` with. If
            not given, resolves relative to `cwd`.

    Returns: Absolute version of `path`, relative to `root_path` if given, 
        otherwise relative to `os.getcwd`.
    """
    def _expandvars(path, env_dict):
        """Expand quoted variables of the form $key and ${key} in path,
        where key is a key in env_dict, similar to os.path.expandvars.

        See `<https://stackoverflow.com/a/30777398>`__; specialize to not skipping
        escaped characters and not changing unrecognized variables.
        """
        return re.sub(
            r'\$(\w+|\{([^}]*)\})', 
            lambda m: env_dict.get(m.group(2) or m.group(1), m.group(0)), 
            path
        )

    if path == '':
        return path # default value set elsewhere
    path = os.path.expanduser(path) # resolve '~' to home dir
    path = os.path.expandvars(path) # expand $VAR or ${VAR} for shell envvars
    if isinstance(env, dict):
        path = _expandvars(path, env)
    if '$' in path:
        _log.error("Couldn't resolve all env vars in '%s'", path)
        return path
    if os.path.isabs(path):
        return path
    if root_path == "":
        root_path = os.getcwd()
    assert os.path.isabs(root_path)
    return os.path.normpath(os.path.join(root_path, path))

def get_available_programs():
    return {'py': 'python', 'ncl': 'ncl', 'R': 'Rscript'}
    #return {'py': sys.executable, 'ncl': 'ncl'}  

def setenv(varname, varvalue, env_dict, overwrite=True):
    """Wrapper to set environment variables.

    Args:
        varname (:obj:`str`): Variable name to define
        varvalue: Value to assign. Coerced to type :obj:`str` before being set.
        env_dict (:obj:`dict`): XXX
        overwrite (:obj:`bool`): If set to `False`, do not overwrite the values
            of previously-set variables. 
    """
    if (not overwrite) and varname in env_dict: 
        _log.debug("Not overwriting ENV %s=%s", varname, env_dict[varname])
    else:
        if 'varname' in env_dict and env_dict[varname] != varvalue: 
            _log.debug(
                "WARNING: setenv %s=%s overriding previous setting %s",
                varname, varvalue, env_dict[varname]
            )
        env_dict[varname] = varvalue

        # environment variables must be strings
        if isinstance(varvalue, bool):
            if varvalue == True:
                varvalue = '1'
            else:
                varvalue = '0'
        elif not isinstance(varvalue, str):
            varvalue = str(varvalue)
        os.environ[varname] = varvalue

        _log.debug("ENV %s=%s", varname, env_dict[varname])

def check_required_envvar(*varlist):
    varlist = varlist[0]   #unpack tuple
    for n, var_n in enumerate(varlist):
        _log.debug("checking envvar %s %s", n, var_n)
        try:
            _ = os.environ[var_n]
        except KeyError:
            _log.exception("Environment variable %s not found.", var_n)
            raise

def check_required_dirs(already_exist =[], create_if_nec = []):
    # arguments can be envvar name or just the paths
    for dir_in in already_exist + create_if_nec : 
        _log.debug("Looking at %s", dir_in)
        if dir_in in os.environ:  
            dir_ = os.environ[dir_in]
        else:
            _log.debug("Envvar %s not defined", dir_in)    
            dir_ = dir_in

        if not os.path.exists(dir_):
            if not dir_in in create_if_nec:
                _log.error("%s=%s directory does not exist", dir_in, dir_)
                raise exc.MDTFFileNotFoundError(dir_)
            else:
                _log.info("Creating %s", dir_)
                os.makedirs(dir_)
        else:
            _log.info("Found %s", dir_)

def bump_version(path, new_v=None, extra_dirs=[]):
    # return a filename that doesn't conflict with existing files.
    # if extra_dirs supplied, make sure path doesn't conflict with pre-existing
    # files at those locations either.
    def _split_version(file_):
        match = re.match(r"""
            ^(?P<file_base>.*?)   # arbitrary characters (lazy match)
            (\.v(?P<version>\d+))  # literal '.v' followed by digits
            ?                      # previous group may occur 0 or 1 times
            $                      # end of string
            """, file_, re.VERBOSE)
        if match:
            return (match.group('file_base'), match.group('version'))
        else:
            return (file_, '')

    def _reassemble(dir_, file_, version, ext_, final_sep):
        if version:
            file_ = ''.join([file_, '.v', str(version), ext_])
        else:
            # get here for version == 0, '' or None
            file_ = ''.join([file_, ext_])
        return os.path.join(dir_, file_) + final_sep

    def _path_exists(dir_list, file_, new_v, ext_, sep):
        new_paths = [_reassemble(d, file_, new_v, ext_, sep) for d in dir_list]
        return any([os.path.exists(p) for p in new_paths])

    if path.endswith(os.sep):
        # remove any terminating slash on directory
        path = path.rstrip(os.sep)
        final_sep = os.sep
    else:
        final_sep = ''
    dir_, file_ = os.path.split(path)
    dir_list = funcs.to_iter(extra_dirs)
    dir_list.append(dir_)
    file_, old_v = _split_version(file_)
    if not old_v:
        # maybe it has an extension and then a version number
        file_, ext_ = os.path.splitext(file_)
        file_, old_v = _split_version(file_)
    else:
        ext_ = ''

    if new_v is not None:
        # removes version if new_v ==0
        new_path = _reassemble(dir_, file_, new_v, ext_, final_sep)
    else:
        if not old_v:
            new_v = 0
        else:
            new_v = int(old_v)
        while _path_exists(dir_list, file_, new_v, ext_, final_sep):
            new_v = new_v + 1
        new_path = _reassemble(dir_, file_, new_v, ext_, final_sep)
    return (new_path, new_v)

class _DoubleBraceTemplate(string.Template):
    """Private class used by :func:`~util_mdtf.append_html_template` to do 
    string templating with double curly brackets as delimiters, since single
    brackets are also used in css.

    See `https://docs.python.org/3.7/library/string.html#string.Template`_ and 
    `https://stackoverflow.com/a/34362892`__.
    """
    flags = re.VERBOSE # matching is case-sensitive, unlike default
    delimiter = '{{' # starting delimter is two braces, then apply
    pattern = r"""
        \{\{(?:                 # match delimiter itself, but don't include it
        # Alternatives for what to do with string following delimiter:
        # case 1) text is an escaped double bracket, written as '{{{{'.
        (?P<escaped>\{\{)|
        # case 2) text is the name of an env var, possibly followed by whitespace,
        # followed by closing double bracket. Match POSIX env var names,
        # case-sensitive (see https://stackoverflow.com/a/2821183), with the 
        # addition that hyphens are allowed.
        # Can't tell from docs what the distinction between <named> and <braced> is.
        \s*(?P<named>[a-zA-Z_][a-zA-Z0-9_-]*)\s*\}\}|
        \s*(?P<braced>[a-zA-Z_][a-zA-Z0-9_-]*)\s*\}\}|
        # case 3) none of the above: ignore & move on (when using safe_substitute)
        (?P<invalid>)
        )
    """

def append_html_template(template_file, target_file, template_dict={}, 
    create=True, append=True):
    """Perform subtitutions on template_file and write result to target_file.

    Variable substitutions are done with custom 
    `templating <https://docs.python.org/3.7/library/string.html#template-strings>`__,
    replacing *double* curly bracket-delimited keys with their values in template_dict.
    For example, if template_dict is {'A': 'foo'}, all occurrences of the string
    `{{A}}` in template_file are replaced with the string `foo`. Spaces between
    the braces and variable names are ignored.

    Double-curly-bracketed strings that don't correspond to keys in template_dict are
    ignored (instead of raising a KeyError.)

    Double curly brackets are chosen as the delimiter to match the default 
    syntax of, eg, django and jinja2. Using single curly braces leads to conflicts
    with CSS syntax.

    Args:
        template_file: Path to template file.
        target_file: Destination path for result. 
        template_dict: :py:obj:`dict` of variable name-value pairs. Both names
            and values must be strings.
        create: Boolean, default True. If true, create target_file if it doesn't
            exist, otherwise raise an OSError. 
        append: Boolean, default True. If target_file exists and this is true,
            append the substituted contents of template_file to it. If false,
            overwrite target_file with the substituted contents of template_file.
    """
    if not os.path.exists(template_file):
        raise exc.MDTFFileNotFoundError(template_file)
    with io.open(template_file, 'r', encoding='utf-8') as f:
        html_str = f.read()
        html_str = _DoubleBraceTemplate(html_str).safe_substitute(template_dict)
    if not os.path.exists(target_file):
        if create:
            _log.debug("Write %s to new %s", template_file, target_file)
            mode = 'w'
        else:
            raise exc.MDTFFileNotFoundError(target_file)
    else:
        if append:
            _log.debug("Append %s to %s", template_file, target_file)
            mode = 'a'
        else:
            _log.debug("Overwrite %s with %s", target_file, template_file)
            os.remove(target_file)
            mode = 'w'
    with io.open(target_file, mode, encoding='utf-8') as f:
        f.write(html_str)
