import collections
import collections.abc
import itertools
import logging

_log = logging.getLogger(__name__)

class _Singleton(type):
    """Private metaclass that creates a :class:`~util.Singleton` base class when
    called. This version is copied from `<https://stackoverflow.com/a/6798042>`__ and
    should be compatible with both Python 2 and 3.
    """
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(_Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

class Singleton(_Singleton('SingletonMeta', (object,), {})): 
    """Parent class defining the 
    `Singleton <https://en.wikipedia.org/wiki/Singleton_pattern>`_ pattern. We
    use this as safer way to pass around global state. See 
    `<https://stackoverflow.com/a/6798042>`__.
    """
    @classmethod
    def _reset(cls):
        """Private method of all :class:`~util.Singleton`-derived classes added
        for use in unit testing only. Calling this method on test teardown 
        deletes the instance, so that tests coming afterward will initialize the 
        :class:`~util.Singleton` correctly, instead of getting the state set 
        during previous tests.
        """
        # pylint: disable=maybe-no-member
        if cls in cls._instances:
            del cls._instances[cls]


class MultiMap(collections.defaultdict):
    """Extension of the :obj:`dict` class that allows doing dictionary lookups 
    from either keys or values. 
    
    Syntax for lookup from keys is unchanged, ``bd['key'] = 'val'``, while lookup
    from values is done on the `inverse` attribute and returns a set of matching
    keys if more than one match is present: ``bd.inverse['val'] = ['key1', 'key2']``.    
    See `<https://stackoverflow.com/a/21894086>`__.
    """
    def __init__(self, *args, **kwargs):
        """Initialize :class:`~util.MultiMap` by passing an ordinary :py:obj:`dict`.
        """
        super(MultiMap, self).__init__(set, *args, **kwargs)
        for key in iter(self.keys()):
            super(MultiMap, self).__setitem__(key, to_iter(self[key], set))

    def __setitem__(self, key, value):
        super(MultiMap, self).__setitem__(key, to_iter(value, set))

    def get_(self, key):
        if key not in list(self.keys()):
            raise KeyError(key)
        return from_iter(self[key])
    
    def to_dict(self):
        d = {}
        for key in iter(self.keys()):
            d[key] = self.get_(key)
        return d

    def inverse(self):
        d = collections.defaultdict(set)
        for key, val_set in iter(self.items()):
            for v in val_set:
                d[v].add(key)
        return dict(d)

    def inverse_get_(self, val):
        # don't raise keyerror if empty; could be appropriate result
        inv_lookup = self.inverse()
        return from_iter(inv_lookup[val])


class NameSpace(dict):
    """ A dictionary that provides attribute-style access.

    For example, `d['key'] = value` becomes `d.key = value`. All methods of 
    :py:obj:`dict` are supported.

    Note: recursive access (`d.key.subkey`, as in C-style languages) is not
        supported.

    Implementation is based on `<https://github.com/Infinidat/munch>`__.
    """

    # only called if k not found in normal places
    def __getattr__(self, k):
        """ Gets key if it exists, otherwise throws AttributeError.
            nb. __getattr__ is only called if key is not found in normal places.
        """
        try:
            # Throws exception if not in prototype chain
            return object.__getattribute__(self, k)
        except AttributeError:
            try:
                return self[k]
            except KeyError:
                raise AttributeError(k)

    def __setattr__(self, k, v):
        """ Sets attribute k if it exists, otherwise sets key k. A KeyError
            raised by set-item (only likely if you subclass NameSpace) will
            propagate as an AttributeError instead.
        """
        try:
            # Throws exception if not in prototype chain
            object.__getattribute__(self, k)
        except AttributeError:
            try:
                self[k] = v
            except Exception:
                raise AttributeError(k)
        else:
            object.__setattr__(self, k, v)

    def __delattr__(self, k):
        """ Deletes attribute k if it exists, otherwise deletes key k. A KeyError
            raised by deleting the key--such as when the key is missing--will
            propagate as an AttributeError instead.
        """
        try:
            # Throws exception if not in prototype chain
            object.__getattribute__(self, k)
        except AttributeError:
            try:
                del self[k]
            except KeyError:
                raise AttributeError(k)
        else:
            object.__delattr__(self, k)

    def __dir__(self):
        return list(self.keys())
    __members__ = __dir__  # for python2.x compatibility

    def __repr__(self):
        """ Invertible* string-form of a Munch.
            (*) Invertible so long as collection contents are each repr-invertible.
        """
        return '{0}({1})'.format(self.__class__.__name__, dict.__repr__(self))

    def __getstate__(self):
        """ Implement a serializable interface used for pickling.
        See `<https://docs.python.org/3.6/library/pickle.html>`__.
        """
        return {k: v for k, v in iter(self.items())}

    def __setstate__(self, state):
        """ Implement a serializable interface used for pickling.
        See `<https://docs.python.org/3.6/library/pickle.html>`__.
        """
        self.clear()
        self.update(state)

    def toDict(self):
        """ Recursively converts a NameSpace back into a dictionary.
        """
        return type(self)._toDict(self)

    @classmethod
    def _toDict(cls, x):
        """ Recursively converts a NameSpace back into a dictionary.
            nb. As dicts are not hashable, they cannot be nested in sets/frozensets.
        """
        if isinstance(x, dict):
            return dict((k, cls._toDict(v)) for k, v in iter(x.items()))
        elif isinstance(x, (list, tuple)):
            return type(x)(cls._toDict(v) for v in x)
        else:
            return x

    @property
    def __dict__(self):
        return self.toDict()

    @classmethod
    def fromDict(cls, x):
        """ Recursively transforms a dictionary into a NameSpace via copy.
            nb. As dicts are not hashable, they cannot be nested in sets/frozensets.
        """
        if isinstance(x, dict):
            return cls((k, cls.fromDict(v)) for k, v in iter(x.items()))
        elif isinstance(x, (list, tuple)):
            return type(x)(cls.fromDict(v) for v in x)
        else:
            return x

    def copy(self):
        return type(self).fromDict(self)
    __copy__ = copy

    def _freeze(self):
        """Return immutable representation of (current) attributes.

        We do this to enable comparison of two Namespaces, which otherwise would 
        be done by the default method of testing if the two objects refer to the
        same location in memory.
        See `<https://stackoverflow.com/a/45170549>`__.
        """
        d = self.toDict()
        d2 = {k: repr(d[k]) for k in d}
        FrozenNameSpace = collections.namedtuple(
            'FrozenNameSpace', sorted(list(d.keys()))
        )
        return FrozenNameSpace(**d2)

    def __eq__(self, other):
        if type(other) is type(self):
            return (self._freeze() == other._freeze())
        else:
            return False

    def __ne__(self, other):
        return (not self.__eq__(other)) # more foolproof

    def __hash__(self):
        return hash(self._freeze())

# ------------------------------------

def is_iterable(obj):
    return isinstance(obj, collections.abc.Iterable) \
        and not isinstance(obj, str) # py3 strings have __iter__

def to_iter(obj, coll_type=list):
    assert coll_type in [list, set, tuple] # only supported types for now
    if obj is None:
        return coll_type([])
    elif isinstance(obj, coll_type):
        return obj
    elif is_iterable(obj):
        return coll_type(obj)
    else:
        return coll_type([obj])

def from_iter(obj):
    if is_iterable(obj):
        if len(obj) == 1:
            return list(obj)[0]
        else:
            return list(obj)
    else:
        return obj

def filter_kwargs(kwarg_dict, function):
    """Given a dict of kwargs, return only those kwargs accepted by function.
    """
    named_args = set(function.__code__.co_varnames)
    # if 'kwargs' in named_args:
    #    return kwarg_dict # presumably can handle anything
    return dict((k, kwarg_dict[k]) for k in named_args \
        if k in kwarg_dict and k not in ['self', 'args', 'kwargs'])

def splice_into_list(list_, splice_d,  key_fn=None):
    """Splice sub-lists in ``splice_d`` into list ``list_`` after their 
    corresponding entries (keys of ``slice_d``). Example: 

    .. code-block:: python
       >>> splice_into_list(['a','b','c'], {'b': ['b1', 'b2']})
       ['a', 'b', 'b1', 'b2', 'c']

    Args:
        list_: parent list to splice sub-lists into.
        splice_d: dict of sub-lists to splice in. Keys are entries in ``list_``
            and values are the sub-lists to insert after that entry. Duplicate
            or missing entries are handled appropriately.
        key_fn (optional): If supplied, function applied to elements of ``list_`` 
            to compare to keys of ``splice_d``.

    Returns: spliced ``list_`` as described above.
    """
    if key_fn is None:
        key_fn = lambda x: x
    chunks = [0, len(list_)]
    for k in splice_d:
        idx = [i + 1 for i,el in enumerate(list_) if key_fn(el) == k]
        if len(idx) != 1:
            _log.debug('%s not unique in %s.', k, str(list_))
        chunks.extend(idx)
    chunk_0, chunk_1 = itertools.tee(sorted(chunks))
    next(chunk_1, None)
    chunks = [list_[c[0]:c[1]] for c in zip(chunk_0, chunk_1)]
    spliced_chunks = []
    for c in chunks:
        if c and key_fn(c[-1]) in splice_d:
            spliced_chunks.append(c + splice_d[key_fn(c[-1])])
        else:
            spliced_chunks.append(c)
    return list(itertools.chain.from_iterable(spliced_chunks))

def deserialize_class(name):
    """Given the name of a currently defined class, return the class itself.
    This avoids security issues with calling :py:func:`eval`. Based on 
    `https://stackoverflow.com/a/11781721`__.

    Args:
        name (str): name of the class to look up.

    Returns: class with the given name, or raise ValueError.
    """
    try:
        # for performance, search python builtin types first before going 
        # through everything
        return getattr(__builtins__, name)
    except AttributeError:
        _log.debug('%s not found in builtin types.', name)
    q = collections.deque([object])
    while q:
        t = q.popleft()
        if t.__name__ == name:
            return t
        try: # keep looking
            q.extend(t.__subclasses__())
        except TypeError:
            # type.__subclasses__ needs an argument, for whatever reason.
            if t is type:
                continue
            else:
                raise
    else:
        raise ValueError('No such type: %r' % name)
