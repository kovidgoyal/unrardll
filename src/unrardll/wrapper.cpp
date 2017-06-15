/*
 * wrapper.cpp
 * Copyright (C) 2017 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD license.
 */

#define _UNICODE
#define UNICODE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <unrar/dll.hpp>


#define STRFY(x) #x
#define STRFY2(x) STRFY(x)
#define NOMEM PyErr_SetString(PyExc_MemoryError, "Out of memory at line number: " STRFY2(__LINE__))

static wchar_t *
unicode_to_wchar(PyObject *o) {
    wchar_t *buf;
    Py_ssize_t len;
    if (o == NULL) return NULL;
    if (!PyUnicode_Check(o)) {PyErr_Format(PyExc_TypeError, "The python object must be a unicode object"); return NULL;}
    len = PyUnicode_GET_SIZE(o);
    buf = (wchar_t *)calloc(len+2, sizeof(wchar_t));
    if (buf == NULL) { NOMEM; return NULL; }
#if PY_MAJOR_VERSION >= 3
    len = PyUnicode_AsWideChar(o, buf, len);
#else
    len = PyUnicode_AsWideChar((PyUnicodeObject*)o, buf, len);
#endif
    if (len == -1) { free(buf); PyErr_Format(PyExc_TypeError, "Invalid python unicode object."); return NULL; }
    return buf;
}

static PyObject *
wchar_to_unicode(const wchar_t *o) {
    PyObject *ans;
    if (o == NULL) return NULL;
    ans = PyUnicode_FromWideChar(o, wcslen(o));
    if (ans == NULL) NOMEM;
    return ans;
}


// Boilerplate {{{
struct module_state {
    PyObject *error;
};

#if PY_MAJOR_VERSION >= 3
#define GETSTATE(m) ((struct module_state*)PyModule_GetState(m))
#else
#define GETSTATE(m) (&_state)
static struct module_state _state;
#endif

static PyMethodDef methods[] = {
    {NULL, NULL}
};

#if PY_MAJOR_VERSION >= 3

static int 
traverse(PyObject *m, visitproc visit, void *arg) {
    Py_VISIT(GETSTATE(m)->error);
    return 0;
}

static int 
clear(PyObject *m) {
    Py_CLEAR(GETSTATE(m)->error);
    return 0;
}


static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "unrar",
        NULL,
        sizeof(struct module_state),
        methods,
        NULL,
        traverse,
        clear,
        NULL
};

#define INITERROR return NULL

PyMODINIT_FUNC
PyInit_unrar(void)

#else
#define INITERROR return

PyMODINIT_FUNC
initunrar(void)
#endif
{
#if PY_MAJOR_VERSION >= 3
    PyObject *module = PyModule_Create(&moduledef);
#else
    PyObject *module = Py_InitModule("unrar", methods);
#endif

    if (module == NULL) { INITERROR; }
    struct module_state *st = GETSTATE(module);

    st->error = PyErr_NewException((char*)"unrar.UNRARError", NULL, NULL);
    if (st->error == NULL) {
        Py_DECREF(module);
        INITERROR;
    }

#if PY_MAJOR_VERSION >= 3
    return module;
#endif
}
// }}}
