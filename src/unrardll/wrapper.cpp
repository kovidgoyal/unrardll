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

static PyObject *UNRARError = NULL;

static inline void
convert_rar_error(unsigned int code) {
#define CASE(x) case x: PyErr_SetString(UNRARError, "Error from unrar dll: " #x); break;
    switch(code) {
        CASE(ERAR_SUCCESS)             
        CASE(ERAR_END_ARCHIVE)        
        CASE(ERAR_NO_MEMORY)          
        CASE(ERAR_BAD_DATA)           
        CASE(ERAR_BAD_ARCHIVE)        
        CASE(ERAR_UNKNOWN_FORMAT)     
        CASE(ERAR_EOPEN)              
        CASE(ERAR_ECREATE)            
        CASE(ERAR_ECLOSE)             
        CASE(ERAR_EREAD)              
        CASE(ERAR_EWRITE)             
        CASE(ERAR_SMALL_BUF)          
        CASE(ERAR_UNKNOWN)            
        CASE(ERAR_MISSING_PASSWORD)   
        CASE(ERAR_EREFERENCE)         
        CASE(ERAR_BAD_PASSWORD)       

        default:
            PyErr_SetString(UNRARError, "Unknown error");
            break;
    }
#undef CASE
}

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

static const char* NAME = "RARFileHandle";

static void 
close_encapsulated_file(PyObject *capsule) {
    HANDLE file = (HANDLE)PyCapsule_GetPointer(capsule, NAME);
    if (file != NULL) RARCloseArchive(file);
}


static inline PyObject*
encapsulate(HANDLE file) {
    PyObject *ans = NULL;
    if (!file) return NULL;
    ans = PyCapsule_New(file, NAME, close_encapsulated_file);
    if (ans == NULL) { RARCloseArchive(file); return NULL; }
    return ans;
}


static PyObject*
open_archive(PyObject *self, PyObject *args) {
    PyObject *path = NULL, *extract = NULL;
    RAROpenArchiveDataEx open_info = {0};
    RARHeaderDataEx file_info = {0};
    HANDLE rar_file = 0;

    if (!PyArg_ParseTuple(args, "O!O", PyUnicode_Type, &path, &extract)) return NULL;
    open_info.OpenMode = PyObject_IsTrue(extract) ? RAR_OM_EXTRACT : RAR_OM_LIST;
    open_info.ArcNameW = unicode_to_wchar(path);
    if (open_info.ArcNameW == NULL)  goto end;

    rar_file = RAROpenArchiveEx(&open_info);
    if (!rar_file) {
        convert_rar_error(open_info.OpenResult);
        goto end;
    }

end:
    free(open_info.ArcNameW);
    return encapsulate(rar_file);
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
    {"open_archive", (PyCFunction)open_archive, METH_VARARGS,
        "open_archive(path, extract=False)\n\nOpen the RAR archive at path. By default opens for listing, use extract=True to open for extraction."
    },


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
    UNRARError = st->error;

#if PY_MAJOR_VERSION >= 3
    return module;
#endif
}
// }}}
