#! /usr/bin/env python
"""Generate JS code from an ASDL description."""

import os, sys

import asdl

TABSIZE = 4
MAX_COL = 120

reserved_words = set(['arguments', 'module'])

def sanitize_name(name):
    if name in reserved_words:
        return '_' + name
    return name

def get_js_type(name):
    """Return a string for the JS name of the type.

    This function special cases the default types provided by asdl.
    """
    if name in asdl.builtin_types:
        return name
    else:
        return "%s_ty" % name

def reflow_lines(s, depth):
    """Reflow the line s indented depth tabs.

    Return a sequence of lines where no line extends beyond MAX_COL
    when properly indented.  The first line is properly indented based
    exclusively on depth * TABSIZE.  All following lines -- these are
    the reflowed lines generated by this function -- start at the same
    column as the first character beyond the opening { in the first
    line.
    """
    size = MAX_COL - depth * TABSIZE
    if len(s) < size:
        return [s]

    lines = []
    cur = s
    padding = ""
    while len(cur) > size:
        i = cur.rfind(' ', 0, size)
        # XXX this should be fixed for real
        if i == -1 and 'GeneratorExp' in cur:
            i = size + 3
        assert i != -1, "Impossible line %d to reflow: %r" % (size, s)
        lines.append(padding + cur[:i])
        if len(lines) == 1:
            # find new size based on brace
            j = cur.find('{', 0, i)
            if j >= 0:
                j += 2 # account for the brace and the space after it
                size -= j
                padding = " " * j
            else:
                j = cur.find('(', 0, i)
                if j >= 0:
                    j += 1 # account for the paren (no space after it)
                    size -= j
                    padding = " " * j
        cur = cur[i+1:]
    else:
        lines.append(padding + cur)
    return lines

def is_simple(sum):
    """Return True if a sum is a simple.

    A sum is simple if its types have no fields, e.g.
    unaryop = Invert | Not | UAdd | USub
    """
    for t in sum.types:
        if t.fields:
            return False
    return True


class EmitVisitor(asdl.VisitorBase):
    """Visit that emits lines"""

    def __init__(self, file):
        self.file = file
        self.identifiers = set()
        super(EmitVisitor, self).__init__()

    def emit_identifier(self, name):
        name = str(name)
        if sanitize_name(name) in self.identifiers:
            return
        self.emit("var %s = '%s'" % (sanitize_name(name), name), 0)
        self.identifiers.add(sanitize_name(name))

    def emit(self, s, depth, reflow=True):
        # XXX reflow long lines?
        if reflow:
            lines = reflow_lines(s, depth)
        else:
            lines = [s]
        for line in lines:
            line = (" " * TABSIZE * depth) + line + "\n"
            self.file.write(line)


class TypeDefVisitor(EmitVisitor):
    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type, depth=0):
        self.visit(type.value, type.name, depth)

    def visitSum(self, sum, name, depth):
        if is_simple(sum):
            self.simple_sum(sum, name, depth)
        else:
            self.sum_with_constructors(sum, name, depth)

    def simple_sum(self, sum, name, depth):
        enum = []
        for i in range(len(sum.types)):
            type = sum.types[i]
            self.emit("var %s = %d // enum %s?" % (type.name, i + 1, name), depth)
        self.emit("", depth)

    def sum_with_constructors(self, sum, name, depth):
        ctype = get_js_type(name)
        s = "var %(ctype)s = _%(name)s // typedef?" % locals()
        self.emit(s, depth)
        self.emit("", depth)

    def visitProduct(self, product, name, depth):
        ctype = get_js_type(name)
        s = "var %(ctype)s = _%(name)s // typedef?" % locals()
        self.emit(s, depth)
        self.emit("", depth)


class StructVisitor(EmitVisitor):
    """Visitor to generate typedefs for AST."""

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type, depth=0):
        self.visit(type.value, type.name, depth)

    def visitSum(self, sum, name, depth):
        if not is_simple(sum):
            self.sum_with_constructors(sum, name, depth)

    def sum_with_constructors(self, sum, name, depth):
        def emit(s, depth=depth):
            self.emit(s % sys._getframe(1).f_locals, depth)
        enum = []
        for i in range(len(sum.types)):
            type = sum.types[i]
            enum.append("%s_kind=%d" % (type.name, i + 1))

        emit("enum _%(name)s_kind {" + ", ".join(enum) + "}")

        emit("var _%(name)s {")
        emit("enum _%(name)s_kind kind", depth + 1)
        emit("union {", depth + 1)
        for t in sum.types:
            self.visit(t, depth + 2)
        emit("} v", depth + 1)
        for field in sum.attributes:
            # rudimentary attribute handling
            type = str(field.type)
            assert type in asdl.builtin_types, type
            emit("%s %s" % (type, field.name), depth + 1)
        emit("}")
        emit("")

    def visitConstructor(self, cons, depth):
        if cons.fields:
            self.emit("var %s = function() {" % cons.name, depth)
            for f in cons.fields:
                self.visit(f, depth + 1)
            self.emit("}", depth)
            self.emit("", depth)

    def visitField(self, field, depth):
        # XXX need to lookup field.type, because it might be something
        # like a builtin...
        ctype = get_js_type(field.type)
        name = field.name
        self.emit("var %(name)s" % locals(), depth)

    def visitProduct(self, product, name, depth):
        self.emit("var _%(name)s  = function() {" % locals(), depth)
        for f in product.fields:
            self.visit(f, depth + 1)
        for field in product.attributes:
            # rudimentary attribute handling
            type = str(field.type)
            assert type in asdl.builtin_types, type
            self.emit("this.%s = null" % (field.name), depth + 1)
        self.emit("}", depth)
        self.emit("", depth)


class PrototypeVisitor(EmitVisitor):
    """Generate function prototypes for the .h file"""

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type):
        self.visit(type.value, type.name)

    def visitSum(self, sum, name):
        if is_simple(sum):
            pass # XXX
        else:
            for t in sum.types:
                self.visit(t, name, sum.attributes)

    def get_args(self, fields):
        """Return list of C argument into, one for each field.

        Argument info is 3-tuple of a C type, variable name, and flag
        that is true if type can be null.
        """
        args = []
        unnamed = {}
        for f in fields:
            if f.name is None:
                name = f.type
                c = unnamed[name] = unnamed.get(name, 0) + 1
                if c > 1:
                    name = "name%d" % (c - 1)
            else:
                name = f.name
            # XXX should extend get_js_type() to handle this
            if f.seq:
                if f.type == 'cmpop':
                    ctype = "var"
                else:
                    ctype = "var"
            else:
                ctype = get_js_type(f.type)
            args.append((ctype, name, f.opt or f.seq))
        return args

    def visitConstructor(self, cons, type, attrs):
        args = self.get_args(cons.fields)
        attrs = self.get_args(attrs)
        ctype = get_js_type(type)
        self.emit_function(cons.name, ctype, args, attrs)

    def emit_function(self, name, ctype, args, attrs, union=True):
        args = args + attrs
        if args:
            argstr = ", ".join(["%s" % aname
                                for _, aname, opt in args])
        else:
            argstr = ""
        margs = "a0"
        for i in range(1, len(args)+1):
            margs += ", a%d" % i
        self.emit("# define %s(%s) _Py_%s(%s)" % (name, margs, name, margs), 0,
                reflow=False)
        self.emit("# %s _Py_%s(%s)" % (ctype, name, argstr), False)

    def visitProduct(self, prod, name):
        self.emit_function(name, get_js_type(name),
                           self.get_args(prod.fields),
                           self.get_args(prod.attributes),
                           union=False)


class FunctionVisitor(PrototypeVisitor):
    """Visitor to generate constructor functions for AST."""

    def emit_function(self, name, ctype, args, attrs, union=True):
        def emit(s, depth=0, reflow=True):
            self.emit(s, depth, reflow)
        argstr = ", ".join(["%s" % aname
                            for _, aname, opt in args + attrs])
        emit("var %s = function(%s) {" % (sanitize_name(name), argstr))
        for argtype, argname, opt in args:
            if not opt and argtype != "int":
                emit("if (!%s) {" % argname, 1)
                emit("throw new exceptions.ValueError(", 2)
                msg = "field %s is required for %s" % (argname, name)
                emit('                "%s")' % msg,
                     2, reflow=False)
                emit('}', 1)

        if union:
            self.emit_body_union(name, args, attrs)
        else:
            self.emit_body_struct(name, args, attrs)
        emit("return p", 1)
        emit("}")
        emit("")

    def emit_body_union(self, name, args, attrs):
        def emit(s, depth=0, reflow=True):
            self.emit(s, depth, reflow)
        emit("p.kind = %s_kind" % name, 1)
        for argtype, argname, opt in args:
            emit("p.v.%s.%s = %s" % (name, argname, argname), 1)
        for argtype, argname, opt in attrs:
            emit("p.%s = %s" % (argname, argname), 1)

    def emit_body_struct(self, name, args, attrs):
        def emit(s, depth=0, reflow=True):
            self.emit(s, depth, reflow)
        for argtype, argname, opt in args:
            emit("p.%s = %s" % (argname, argname), 1)
        for argtype, argname, opt in attrs:
            emit("p.%s = %s" % (argname, argname), 1)


class PickleVisitor(EmitVisitor):

    def visitModule(self, mod):
        for dfn in mod.dfns:
            self.visit(dfn)

    def visitType(self, type):
        self.visit(type.value, type.name)

    def visitSum(self, sum, name):
        pass

    def visitProduct(self, sum, name):
        pass

    def visitConstructor(self, cons, name):
        pass

    def visitField(self, sum):
        pass


class Obj2ModPrototypeVisitor(PickleVisitor):
    def visitProduct(self, prod, name):
        pass

    visitSum = visitProduct


class Obj2ModVisitor(PickleVisitor):
    def funcHeader(self, name):
        ctype = get_js_type(name)
        self.emit("var obj2ast_%s = function(obj) {" % (name), 0)
        self.emit("var isinstance", 1)
        self.emit("", 0)

    def sumTrailer(self, name, add_label=False):
        self.emit("", 0)
        # there's really nothing more we can do if this fails ...
        error = "expected some sort of %s, but got %%R" % name
        format = "PyErr_Format(PyExc_TypeError, \"%s\", obj)"
        self.emit(format % error, 1, reflow=False)
        self.emit("return 1", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def simpleSum(self, sum, name):
        self.funcHeader(name)
        for t in sum.types:
            line = "isinstance = types.isinstance(obj, %s_type)"
            self.emit(line % (t.name,), 1)
            self.emit("if (isinstance == -1) {", 1)
            self.emit("return 1", 2)
            self.emit("}", 1)
            self.emit("if (isinstance) {", 1)
            self.emit("out = %s" % t.name, 2)
            self.emit("return 0", 2)
            self.emit("}", 1)
        self.sumTrailer(name)

    def buildArgs(self, fields):
        return ", ".join(fields)

    def complexSum(self, sum, name):
        self.funcHeader(name)
        self.emit("var tmp = null", 1)
        for a in sum.attributes:
            self.visitAttributeDeclaration(a, name, sum=sum)
        self.emit("", 0)
        # XXX: should we only do this for 'expr'?
        self.emit("if (obj == null) {", 1)
        self.emit("out = null", 2)
        self.emit("return 0", 2)
        self.emit("}", 1)
        for a in sum.attributes:
            self.visitField(a, name, sum=sum, depth=1)
        for t in sum.types:
            line = "isinstance = types.isinstance(obj, %s_type)"
            self.emit(line % (t.name,), 1)
            self.emit("if (isinstance == -1) {", 1)
            self.emit("return 1", 2)
            self.emit("}", 1)
            self.emit("if (isinstance) {", 1)
            for f in t.fields:
                self.visitFieldDeclaration(f, t.name, sum=sum, depth=2)
            self.emit("", 0)
            for f in t.fields:
                self.visitField(f, t.name, sum=sum, depth=2)
            args = [f.name for f in t.fields] + [a.name for a in sum.attributes]
            self.emit("out = %s(%s)" % (t.name, self.buildArgs(args)), 2)
            self.emit("if (out == null) return 1", 2)
            self.emit("return 0", 2)
            self.emit("}", 1)
        self.sumTrailer(name, True)

    def visitAttributeDeclaration(self, a, name, sum=sum):
        self.emit("var %s" % (a.name), 1)

    def visitSum(self, sum, name):
        if is_simple(sum):
            self.simpleSum(sum, name)
        else:
            self.complexSum(sum, name)

    def visitProduct(self, prod, name):
        ctype = get_js_type(name)
        self.emit("var obj2ast_%s = function(obj) {" % (name), 0)
        self.emit("var tmp = null", 1)
        for f in prod.fields:
            self.visitFieldDeclaration(f, name, prod=prod, depth=1)
        for a in prod.attributes:
            self.visitFieldDeclaration(a, name, prod=prod, depth=1)
        self.emit("", 0)
        for f in prod.fields:
            self.visitField(f, name, prod=prod, depth=1)
        for a in prod.attributes:
            self.visitField(a, name, prod=prod, depth=1)
        args = [f.name for f in prod.fields]
        args.extend([a.name for a in prod.attributes])
        self.emit("out = %s(%s)" % (name, self.buildArgs(args)), 1)
        self.emit("return 0", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def visitFieldDeclaration(self, field, name, sum=None, prod=None, depth=0):
        ctype = get_js_type(field.type)
        if field.seq:
            if self.isSimpleType(field):
                self.emit("var %s" % field.name, depth)
            else:
                self.emit("var %s" % field.name, depth)
        else:
            self.emit("var %s" % (field.name), depth)

    def isSimpleSum(self, field):
        # XXX can the members of this list be determined automatically?
        return field.type in ('expr_context', 'boolop', 'operator',
                              'unaryop', 'cmpop')

    def isNumeric(self, field):
        return get_js_type(field.type) in ("int", "bool")

    def isSimpleType(self, field):
        return self.isSimpleSum(field) or self.isNumeric(field)

    def visitField(self, field, name, sum=None, prod=None, depth=0):
        ctype = get_js_type(field.type)
        if field.opt:
            check = "exists_not_none(obj, PyId_%s)" % (field.name,)
        else:
            check = "_PyObject_HasAttrId(obj, PyId_%s)" % (field.name,)
        self.emit("if (%s) {" % (check,), depth, reflow=False)
        self.emit("var res", depth+1)
        if field.seq:
            self.emit("var len", depth+1)
            self.emit("var i", depth+1)
        self.emit("tmp = _PyObject_GetAttrId(obj, PyId_%s)" % field.name, depth+1)
        self.emit("if (tmp == null) return 1", depth+1)
        if field.seq:
            self.emit("if (!PyList_Check(tmp)) {", depth+1)
            self.emit("throw new exceptions.TypeError(\"%s field \\\"%s\\\" must "
                      "be a list, not a \" + tmp.ob_type.tp_name)" %
                      (name, field.name),
                      depth+2, reflow=False)
            self.emit("return 1", depth+2)
            self.emit("}", depth+1)
            self.emit("len = PyList_GET_SIZE(tmp)", depth+1)
            if self.isSimpleType(field):
                self.emit("%s = _Py_asdl_seq_new(len)" % field.name, depth+1)
            else:
                self.emit("%s = _Py_asdl_seq_new(len)" % field.name, depth+1)
            self.emit("if (%s == null) return 1" % field.name, depth+1)
            self.emit("for (i = 0; i < len; i++) {", depth+1)
            self.emit("var value", depth+2)
            self.emit("res = obj2ast_%s(PyList_GET_ITEM(tmp, i), value)" %
                      field.type, depth+2, reflow=False)
            self.emit("if (res != 0) return 1", depth+2)
            self.emit("asdl_seq_SET(%s, i, value)" % field.name, depth+2)
            self.emit("}", depth+1)
        else:
            self.emit("res = obj2ast_%s(tmp, %s)" %
                      (field.type, field.name), depth+1)
            self.emit("if (res != 0) return 1", depth+1)

        self.emit("tmp.clear()", depth+1)
        self.emit("} else {", depth)
        if not field.opt:
            message = "required field \\\"%s\\\" missing from %s" % (field.name, name)
            format = "throw new exceptions.TypeError(\"%s\")"
            self.emit(format % message, depth+1, reflow=False)
            self.emit("return 1", depth+1)
        else:
            if self.isNumeric(field):
                self.emit("%s = 0" % field.name, depth+1)
            elif not self.isSimpleType(field):
                self.emit("%s = null" % field.name, depth+1)
            else:
                raise TypeError("could not determine the default value for %s" % field.name)
        self.emit("}", depth)


class MarshalPrototypeVisitor(PickleVisitor):

    def prototype(self, sum, name):
        pass

    visitProduct = visitSum = prototype


class PyTypesDeclareVisitor(PickleVisitor):

    def visitProduct(self, prod, name):
        self.emit("function %s_type() {}" % name, 0)
        if prod.attributes:
            for a in prod.attributes:
                self.emit_identifier(a.name)
            self.emit("var %s_attributes = [" % name, 0)
            for a in prod.attributes:
                self.emit('"%s",' % a.name, 1)
            self.emit("]", 0)
        if prod.fields:
            for f in prod.fields:
                self.emit_identifier(sanitize_name(f.name))
            self.emit("var %s_fields =[" % name,0)
            for f in prod.fields:
                self.emit('"%s",' % f.name, 1)
            self.emit("]", 0)

    def visitSum(self, sum, name):
        self.emit("function %s_type() {}" % name, 0)
        if sum.attributes:
            for a in sum.attributes:
                self.emit_identifier(a.name)
            self.emit("var %s_attributes = [" % name, 0)
            for a in sum.attributes:
                self.emit('"%s",' % a.name, 1)
            self.emit("]", 0)
        ptype = ""
        if is_simple(sum):
            ptype = get_js_type(name)
            tnames = []
            for t in sum.types:
                tnames.append(str(t.name)+"_singleton")
            tnames = ", *".join(tnames)
        for t in sum.types:
            self.visitConstructor(t, name)

    def visitConstructor(self, cons, name):
        self.emit("function %s_type() {}" % cons.name, 0)
        if cons.fields:
            for t in cons.fields:
                self.emit_identifier(t.name)
            self.emit("var %s_fields = [" % cons.name, 0)
            for t in cons.fields:
                self.emit('"%s",' % t.name, 1)
            self.emit("]",0)

class PyTypesVisitor(PickleVisitor):

    def visitModule(self, mod):
        self.emit("""

var types = require('../../../types')
var exceptions = require('../../../core/exceptions')

var AST_object = function() {
    this.dict = new types.Dict()
}

AST_object.prototype = Object.create(Object.prototype)
AST_object.prototype.__class__ = new types.Type('AST_object')

AST_object.prototype.traverse = function(visit, arg) {
    Py_VISIT(this.dict)
}

AST_object.prototype.ast_clear = function() {
    this.dict.clear()
}

AST_object.prototype.init = function(args, kwarg) {
    _Py_IDENTIFIER(_fields)
    var i
    var numfields = 0
    var res = -1
    var key = null
    var value = null
    var fields = null
    fields = _PyObject_GetAttrId(Py_TYPE(self), PyId__fields)
    if (!fields)
        PyErr_Clear()
    if (fields) {
        numfields = PySequence_Size(fields)
        if (numfields == -1)
            return res
    }
    res = 0 /* if no error occurs, this stays 0 to the end */
    if (PyTuple_GET_SIZE(args) > 0) {
        if (numfields != PyTuple_GET_SIZE(args)) {
            throw new exceptions.TypeError(Py_TYPE(self).tp_name + " constructor takes " + (numfields == 0 ? "" : "either 0 or ") +
                         numfield + " positional argument " + (numfields == 1 ? "" : "s"))
            res = -1
            return res
        }
        for (i = 0; i < PyTuple_GET_SIZE(args); i++) {
            /* cannot be reached when fields is null */
            var name = PySequence_GetItem(fields, i)
            if (!name) {
                res = -1
                return res
            }
            res = PyObject_SetAttr(self, name, PyTuple_GET_ITEM(args, i))
            if (res < 0)
                return res
        }
    }
    if (kw) {
        i = 0  /* needed by PyDict_Next */
        while (PyDict_Next(kw, i, key, value)) {
            res = PyObject_SetAttr(self, key, value)
            if (res < 0) {
                return res
            }
        }
    }
    return res
}

/* Pickling support */
AST_object.prototype.reduce = function(unused) {
    var res = null
    _Py_IDENTIFIER(__dict__)
    var dict = _PyObject_GetAttrId(self, PyId___dict__)
    if (dict == null) {
        if (PyErr_ExceptionMatches(PyExc_AttributeError))
            PyErr_Clear()
        else
            return null
    }
    if (dict) {
        res = Py_BuildValue("O()O", Py_TYPE(self), dict)
        return res
    }
    return Py_BuildValue("O()", Py_TYPE(self))
}

var make_type = function(type, base, fields) {
    var fnames = new types.Tuple(fields || [])
    return new types.Type('Tokenizer', base, fields)
}

var add_attributes = function(type, attrs) {
    var l = new types.Tuple(attrs || [])
    type._attributes = l
}


/* Conversion AST -> Python */

var ast2obj_list = function(seq, func) {
    var i = 0
    var n = asdl_seq_LEN(seq)
    var result = PyList_New(n)
    var value = null
    if (!result) {
        return null
    }
    for (var i = 0; i < n; i++) {
        value = func(asdl_seq_GET(seq, i))
        if (!value) {
            return null
        }
        PyList_SET_ITEM(result, i, value)
    }
    return result
}

var ast2obj_int = function(b) {
    return new types.Int(b)
}

/* Conversion Python -> AST */

var obj2ast_singleton = function(obj) {
    if (obj != null && !types.isinstance(obj, types.Bool)) {
        throw new exceptions.ValueError("AST singleton must be True, False, or None")
    }
    return obj
}

var obj2ast_object = function(obj) {
    return obj
}

var obj2ast_constant = function(obj) {
    return obj
}

var obj2ast_identifier = function(obj) {
    if (!types.isinstance(obj, types.Str) && obj != null) {
        throw new exceptions.TypeError("AST identifier must be of type str")
    }
    return obj2ast_object(obj)
}

var obj2ast_string = function(obj) {
    if (!types.isinstance(obj, [types.Bytes, types.Str])) {
        throw new exceptions.TypeError("AST string must be of type str")
    }
    return obj2ast_object(obj)
}

var obj2ast_bytes = function(obj) {
    if (!types.isinstance(obj, types.Bytes)) {
        throw new exceptions.TypeError("AST bytes must be of type bytes")
    }
    return obj2ast_object(obj)
}

var obj2ast_int = function(obj) {
    if (!types.isinstance(obj, [types.Int])) {
        throw new exceptions.ValueError("invalid integer value: ", obj)
    }
    return obj.int32()
}

var add_ast_fields = function() {
    var empty_tuple = new types.Tuple()
    AST_object._fields = empty_tuple
    AST_object._attributed = empty_tuple
}

var exists_not_none = function(obj, id) {
    var isnone = null
    var attr = obj.id
    if (!attr) {
        return
    }
    isnone = attr == null
    return !isnone
}

// macros

var asdl_seq = function() {
    this.size = 0
    this.elements = null
}

var SIZE_MAX = (1<<31)-1
var PY_SIZE_MAX = SIZE_MAX

var _Py_asdl_seq_new = function(size) {
    var seq = null

    /* check size is sane */
    if (size < 0 ||
        (size && ((size - 1) > (PY_SIZE_MAX / 4)))) {
        return null
    }

    seq = new asdl_seq()
    seq.size = size
    seq.elements = new Array(size)
    for (var i = 0; i < size; i++) {
        seq.elements[i] = null
    }
    return seq
}

var asdl_seq_GET = function(S, I) {
    return S.elements[I]
}

var asdl_seq_LEN = function(S) {
    return (S == null) ? 0 : S.size
}

var asdl_seq_SET = function(S, I, V) {
    S.elements[I] = V
}


""", 0, reflow=False)

        self.emit("var init_types = function() {",0)
        self.emit("if (this.initialized) return 1", 1)
        self.emit("if (add_ast_fields() < 0) return 0", 1)
        for dfn in mod.dfns:
            self.visit(dfn)
        self.emit("initialized = 1", 1)
        self.emit("return 1", 1)
        self.emit("}", 0)
        self.emit("init_types.initialized = 0", 0)

    def visitProduct(self, prod, name):
        if prod.fields:
            fields = name+"_fields"
        else:
            fields = "null"
        self.emit('%s_type.prototype.__class__ = make_type("%s", AST_object, %s)' %
                        (name, name, fields), 1)
        self.emit("if (!%s_type) return 0" % name, 1)
        if prod.attributes:
            self.emit("add_attributes(%s_type, %s_attributes)" %
                            (name, name), 1)
        else:
            self.emit("add_attributes(%s_type, null, 0)" % name, 1)

    def visitSum(self, sum, name):
        self.emit('%s_type.prototype.__class__ = make_type("%s", AST_object, null)' %
                  (name, name), 1)
        self.emit("if (!%s_type) return 0" % name, 1)
        if sum.attributes:
            self.emit("add_attributes(%s_type, %s_attributes, %d)" %
                            (name, name, len(sum.attributes)), 1)
        else:
            self.emit("add_attributes(%s_type, null, 0)" % name, 1)
        simple = is_simple(sum)
        for t in sum.types:
            self.visitConstructor(t, name, simple)

    def visitConstructor(self, cons, name, simple):
        if cons.fields:
            fields = cons.name+"_fields"
        else:
            fields = "null"
        self.emit('%s_type.prototype.__class__ = make_type("%s", %s_type, %s)' %
                            (cons.name, cons.name, name, fields), 1)
        self.emit("if (!%s_type) return 0" % cons.name, 1)
        if simple:
            self.emit("%s_singleton = new %s_type()" %
                             (cons.name, cons.name), 1)
            self.emit("if (!%s_singleton) return 0" % cons.name, 1)


class ASTModuleVisitor(PickleVisitor):

    def visitModule(self, mod):
        self.emit("function _astmodule() {", 0)
        self.emit("this._ast = null", 1)
        self.emit("}", 0)
        self.emit("var PyInit__ast = function() {", 0)
        self.emit("var m = null", 1)
        self.emit("var d = null", 1)
        self.emit("if (!init_types()) return null", 1)
        self.emit('m = PyModule_Create(_astmodule)', 1)
        self.emit("if (!m) return null", 1)
        self.emit("d = PyModule_GetDict(m)", 1)
        self.emit('if (PyDict_SetItemString(d, "AST", AST_object) < 0) return null', 1)
        self.emit('if (PyModule_AddIntMacro(m, PyCF_ONLY_AST) < 0)', 1)
        self.emit("return null", 2)
        for dfn in mod.dfns:
            self.visit(dfn)
        self.emit("return m", 1)
        self.emit("}", 0)

    def visitProduct(self, prod, name):
        self.addObj(name)

    def visitSum(self, sum, name):
        self.addObj(name)
        for t in sum.types:
            self.visitConstructor(t, name)

    def visitConstructor(self, cons, name):
        self.addObj(cons.name)

    def addObj(self, name):
        self.emit('if (PyDict_SetItemString(d, "%s", %s_type) < 0) return null' % (name, name), 1)


_SPECIALIZED_SEQUENCES = ('stmt', 'expr')

def find_sequence(fields, doing_specialization):
    """Return True if any field uses a sequence."""
    for f in fields:
        if f.seq:
            if not doing_specialization:
                return True
            if str(f.type) not in _SPECIALIZED_SEQUENCES:
                return True
    return False

def has_sequence(types, doing_specialization):
    for t in types:
        if find_sequence(t.fields, doing_specialization):
            return True
    return False


class StaticVisitor(PickleVisitor):
    CODE = '''Very simple, always emit this static code.  Override CODE'''

    def visit(self, object):
        self.emit(self.CODE, 0, reflow=False)


class ObjVisitor(PickleVisitor):

    def func_begin(self, name):
        ctype = get_js_type(name)
        self.emit("var ast2obj_%s = function(_o) {" % (name), 0)
        self.emit("var o = _o", 1)
        self.emit("var result = null", 1)
        self.emit("var value = null", 1)
        self.emit('if (!o) {', 1)
        self.emit('return null', 2)
        self.emit("}", 1)
        self.emit('', 0)

    def func_end(self):
        self.emit("return result", 1)
        self.emit("}", 0)
        self.emit("", 0)

    def visitSum(self, sum, name):
        if is_simple(sum):
            self.simpleSum(sum, name)
            return
        self.func_begin(name)
        self.emit("switch (o.kind) {", 1)
        for i in range(len(sum.types)):
            t = sum.types[i]
            self.visitConstructor(t, i + 1, name)
        self.emit("}", 1)
        for a in sum.attributes:
            self.emit("value = ast2obj_%s(o.%s)" % (a.type, a.name), 1)
            self.emit("if (!value) return null", 1)
            self.emit('if (_PyObject_SetAttrId(result, PyId_%s, value) < 0)' % a.name, 1)
            self.emit('return null', 2)
        self.func_end()

    def simpleSum(self, sum, name):
        self.emit("var ast2obj_%s = function(o) {" % (name), 0)
        self.emit("switch(o) {", 1)
        for t in sum.types:
            self.emit("case %s:" % t.name, 2)
            self.emit("return %s_singleton" % t.name, 3)
        self.emit("default:", 2)
        self.emit('/* should never happen, but just in case ... */', 3)
        code = "PyErr_Format(PyExc_SystemError, \"unknown %s found\")" % name
        self.emit(code, 3, reflow=False)
        self.emit("return null", 3)
        self.emit("}", 1)
        self.emit("}", 0)

    def visitProduct(self, prod, name):
        self.func_begin(name)
        self.emit("result = PyType_GenericNew(%s_type, null, null)" % name, 1)
        self.emit("if (!result) return null", 1)
        for field in prod.fields:
            self.visitField(field, name, 1, True)
        for a in prod.attributes:
            self.emit("value = ast2obj_%s(o.%s)" % (a.type, a.name), 1)
            self.emit("if (!value) return null", 1)
            self.emit('if (_PyObject_SetAttrId(result, PyId_%s, value) < 0)' % a.name, 1)
            self.emit('return null', 2)
        self.func_end()

    def visitConstructor(self, cons, enum, name):
        self.emit("case %s_kind:" % cons.name, 1)
        self.emit("result = PyType_GenericNew(%s_type, null, null)" % cons.name, 2)
        self.emit("if (!result) return null", 2)
        for f in cons.fields:
            self.visitField(f, cons.name, 2, False)
        self.emit("break", 2)

    def visitField(self, field, name, depth, product):
        def emit(s, d):
            self.emit(s, depth + d)
        if product:
            value = "o.%s" % field.name
        else:
            value = "o.v.%s.%s" % (name, field.name)
        self.set(field, value, depth)
        emit("if (!value) return null", 0)
        emit('if (_PyObject_SetAttrId(result, PyId_%s, value) == -1)' % field.name, 0)
        emit("return null", 1)

    def emitSeq(self, field, value, depth, emit):
        emit("seq = %s" % value, 0)
        emit("n = asdl_seq_LEN(seq)", 0)
        emit("value = PyList_New(n)", 0)
        emit("if (!value) return null", 0)
        emit("for (i = 0; i < n; i++) {", 0)
        self.set("value", field, "asdl_seq_GET(seq, i)", depth + 1)
        emit("if (!value1) return null", 1)
        emit("PyList_SET_ITEM(value, i, value1)", 1)
        emit("value1 = null", 1)
        emit("}", 0)

    def set(self, field, value, depth):
        if field.seq:
            # XXX should really check for is_simple, but that requires a symbol table
            if field.type == "cmpop":
                # While the sequence elements are stored as void*,
                # ast2obj_cmpop expects an enum
                self.emit("{", depth)
                self.emit("var i", depth+1)
                self.emit("var n = asdl_seq_LEN(%s)" % value, depth+1)
                self.emit("value = PyList_New(n)", depth+1)
                self.emit("if (!value) return null", depth+1)
                self.emit("for(i = 0; i < n; i++)", depth+1)
                # This cannot fail, so no need for error handling
                self.emit("PyList_SET_ITEM(value, i, ast2obj_cmpop(asdl_seq_GET(%s, i)))" % value,
                          depth+2, reflow=False)
                self.emit("}", depth)
            else:
                self.emit("value = ast2obj_list(%s, ast2obj_%s)" % (value, field.type), depth)
        else:
            ctype = get_js_type(field.type)
            self.emit("value = ast2obj_%s(%s)" % (field.type, value), depth, reflow=False)


class PartingShots(StaticVisitor):

    CODE = """
var PyAST_mod2obj = function(t) {
    if (!init_types()) {
        return null
    }
    return ast2obj_mod(t)
}

/* mode is 0 for "exec", 1 for "eval" and 2 for "single" input */
var PyAST_obj2mod = function(ast, mode) {
    var res
    var req_type = [null, null, null]
    var req_name = ["Module", "Expression", "Interactive"]
    var isinstance

    req_type[0] = Module_type
    req_type[1] = Expression_type
    req_type[2] = Interactive_type

    if (!init_types())
        return null

    isinstance = PyObject_IsInstance(ast, req_type[mode])
    if (isinstance == -1)
        return null
    if (!isinstance) {
        throw new exceptions.TypeError("expected " + req_name[mode] + " node, got " + Py_TYPE(ast).tp_name)
    }
    if (obj2ast_mod(ast, res) != 0) {
        return null
    } else {
        return res
    }
}

var ast_check = function(obj) {
    return types.isinstance(obj, AST_object)
}

module.exports = {
    ast_check: ast_check,
    asdl_seq_SET: asdl_seq_SET,
    asdl_seq_GET: asdl_seq_GET,
    asdl_seq_LEN: asdl_seq_LEN
}
"""

class ChainOfVisitors:
    def __init__(self, *visitors):
        self.visitors = visitors

    def visit(self, object):
        for v in self.visitors:
            v.visit(object)
            v.emit("", 0)

common_msg = """/* File automatically generated by %s. */
"""

def main(srcfile, dump_module=False):
    argv0 = sys.argv[0]
    components = argv0.split(os.sep)
    argv0 = os.sep.join(components[-2:])
    auto_gen_msg = common_msg % argv0
    mod = asdl.parse(srcfile)
    if dump_module:
        print('Parsed Module:')
        print(mod)
    if not asdl.check(mod):
        sys.exit(1)
    if SRC_DIR:
        p = os.path.join(SRC_DIR, str(mod.name) + "-ast.js")
        f = open(p, "w")
        f.write(auto_gen_msg)
        v = ChainOfVisitors(
            PyTypesDeclareVisitor(f),
            PyTypesVisitor(f),
            Obj2ModPrototypeVisitor(f),
            FunctionVisitor(f),
            ObjVisitor(f),
            Obj2ModVisitor(f),
            ASTModuleVisitor(f),
            PartingShots(f),
            )
        v.visit(mod)
        f.close()

if __name__ == "__main__":
    import getopt

    SRC_DIR = ''
    dump_module = False
    opts, args = getopt.getopt(sys.argv[1:], "dc:")
    for o, v in opts:
        if o == '-c':
            SRC_DIR = v
        if o == '-d':
            dump_module = True
    if len(args) != 1:
        print('Must specify single input file')
        sys.exit(1)
    main(args[0], dump_module)
