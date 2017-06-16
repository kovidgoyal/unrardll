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
// We have to include rar.hpp not dll.hpp as the dll.hpp API provides no way to
// extract UTF-16 comments without first roundtripping them through some
// encoding. This is potentially lossy if the encoding, which is system
// dependent, cannot handle all the unicode characters.
#include <unrar/rar.hpp>  

typedef struct {
  CommandData Cmd;
  Archive Arc;
} PartialDataSet;  // taken from dll.cpp


typedef struct {
    PartialDataSet *unrar_data;
    PyObject *callback_object;
    PyGILState_STATE thread_state;
} UnrarOperation;

#define ALLOW_THREADS uo->thread_state = PyGILState_Ensure();
#define BLOCK_THREADS PyGILState_Release(uo->thread_state); 

static int 
RarErrorToDll(RAR_EXIT ErrCode) {
  switch(ErrCode) {
    case RARX_FATAL:
      return ERAR_EREAD;
    case RARX_CRC:
      return ERAR_BAD_DATA;
    case RARX_WRITE:
      return ERAR_EWRITE;
    case RARX_OPEN:
      return ERAR_EOPEN;
    case RARX_CREATE:
      return ERAR_ECREATE;
    case RARX_MEMORY:
      return ERAR_NO_MEMORY;
    case RARX_BADPWD:
      return ERAR_BAD_PASSWORD;
    case RARX_SUCCESS:
      return ERAR_SUCCESS; // 0.
    default:
      return ERAR_UNKNOWN;
  }
}

#define STRFY(x) #x
#define STRFY2(x) STRFY(x)
#define NOMEM PyErr_SetString(PyExc_MemoryError, "Out of memory at line number: " STRFY2(__LINE__))

static PyObject *UNRARError = NULL;

static inline void
convert_rar_error(unsigned int code) {
#define CASE(x) case x: PyErr_SetString(UNRARError, #x); break;
    switch(code) {
        CASE(ERAR_SUCCESS)             
        CASE(ERAR_END_ARCHIVE)        
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

        case ERAR_NO_MEMORY:
            PyErr_NoMemory();
            break;

        default:
            PyErr_SetString(UNRARError, "Unknown error");
            break;
    }
#undef CASE
}

static inline Py_ssize_t
unicode_to_wchar(PyObject *o, wchar_t *buf, Py_ssize_t sz) {
    if (!PyUnicode_Check(o)) {PyErr_Format(PyExc_TypeError, "The python object must be a unicode object"); return -1;}
#if PY_MAJOR_VERSION >= 3
    sz = PyUnicode_AsWideChar(o, buf, sz);
#else
    sz = PyUnicode_AsWideChar((PyUnicodeObject*)o, buf, sz);
#endif
    return sz;
}

static PyObject *
wchar_to_unicode(const wchar_t *o, size_t sz) {
    PyObject *ans;
    if (o == NULL) return NULL;
    ans = PyUnicode_FromWideChar(o, sz);
    if (ans == NULL) NOMEM;
    return ans;
}

#define NAME "RARFileHandle"

static void 
close_encapsulated_file(PyObject *capsule) {
    if (PyCapsule_IsValid(capsule, NAME)) {
        UnrarOperation* uo = (UnrarOperation*)PyCapsule_GetPointer(capsule, NAME);
        if (uo->unrar_data) RARCloseArchive((HANDLE)uo->unrar_data);
        Py_XDECREF(uo->callback_object);
        free(uo);
        PyCapsule_SetName(capsule, NULL); // Invalidate capsule so free is not called twice
    }
}


static inline PyObject*
encapsulate(UnrarOperation* file) {
    PyObject *ans = NULL;
    if (!file) return NULL;
    ans = PyCapsule_New(file, NAME, close_encapsulated_file);
    if (ans == NULL) { RARCloseArchive(file->unrar_data); Py_XDECREF(file->callback_object); free(file); return NULL; }
    return ans;
}


static int CALLBACK
unrar_callback(UINT msg, LPARAM user_data, LPARAM p1, LPARAM p2) {
    int ret = -1;
    UnrarOperation *uo = (UnrarOperation*)user_data;
    PyObject *callback = uo->callback_object;
    switch(msg) {
        case UCM_CHANGEVOLUME:
        case UCM_CHANGEVOLUMEW:
            if (p2 == RAR_VOL_NOTIFY) ret = 0;
            break;
        case UCM_NEEDPASSWORD:
            break;  // we only support unicode passwords, which is fine since unrar asks for those before trying ansi password
        case UCM_NEEDPASSWORDW:
            if (p2 > -1 && callback) {
                BLOCK_THREADS;
                PyObject *pw = PyObject_CallMethod(callback, (char*)"_get_password", NULL);
                if (PyErr_Occurred()) PyErr_Print();
                if (pw && pw != Py_None) {
                    Py_ssize_t sz = unicode_to_wchar(pw, (wchar_t*)p1, p2);
                    Py_DECREF(pw);
                    if (sz > 0) ret = 0;
                }
                ALLOW_THREADS;
            }
            break;
        case UCM_PROCESSDATA:
            if (p2 > -1 && callback) {
                BLOCK_THREADS;
#if PY_MAJOR_VERSION >= 3
                PyObject *pw = PyObject_CallMethod(callback, "_process_data", "y#", (char*)p1, (int)p2);
#else
                PyObject *pw = PyObject_CallMethod(callback, (char*)"_process_data", (char*)"s#", (char*)p1, (int)p2);
#endif
                if (PyErr_Occurred()) PyErr_Print();
                ret = (pw && PyObject_IsTrue(pw)) ? 0 : -1;
                Py_XDECREF(pw);
                ALLOW_THREADS;
            }
            break;
    }
    PyErr_Clear();
    return ret;
}


static PyObject*
open_archive(PyObject *self, PyObject *args) {
    PyObject *path = NULL, *callback = NULL;
    RAROpenArchiveDataEx open_info = {0};
    UnrarOperation *uo = NULL;
    wchar_t pathbuf[NM + 10] = {0};

    if (!PyArg_ParseTuple(args, "O!O|I", &PyUnicode_Type, &path, &callback, &(open_info.OpenMode))) return NULL;
    if (unicode_to_wchar(path, pathbuf, sizeof(pathbuf) / sizeof(pathbuf[0])) < 0) return NULL;
    open_info.Callback = unrar_callback;
    open_info.ArcNameW = pathbuf;
    if (open_info.ArcNameW == NULL)  goto end;
    uo = (UnrarOperation*)calloc(1, sizeof(UnrarOperation));
    if (uo == NULL) { PyErr_NoMemory(); goto end; }
    if (callback) { Py_INCREF(callback); uo->callback_object = callback; }
    open_info.UserData = (LPARAM)uo;

    ALLOW_THREADS;
    uo->unrar_data = (PartialDataSet*)RAROpenArchiveEx(&open_info);
    BLOCK_THREADS;
    if (!uo->unrar_data) {
        Py_XDECREF(uo->callback_object); free(uo); uo = NULL;
        convert_rar_error(open_info.OpenResult);
        goto end;
    }
    if (open_info.OpenResult != ERAR_SUCCESS) {
        RARCloseArchive((HANDLE)uo->unrar_data);
        Py_XDECREF(uo->callback_object); free(uo); uo = NULL;
        convert_rar_error(open_info.OpenResult);
        goto end;
    }

end:
    return encapsulate(uo);
}

static PyObject*
close_archive(PyObject *self, PyObject *capsule) {
    close_encapsulated_file(capsule);
    Py_RETURN_NONE;
}

static inline UnrarOperation*
from_capsule(PyObject *file_capsule) {
    UnrarOperation *data = (UnrarOperation*)PyCapsule_GetPointer(file_capsule, NAME);
    if (data == NULL) {
        PyErr_SetString(PyExc_TypeError, "Not a valid " NAME " capsule");
        return NULL;
    }
    return data;
}

#define FROM_CAPSULE(x) from_capsule(x); if (uo == NULL) return NULL;

static PyObject*
get_comment(PyObject *self, PyObject *file_capsule) {
    UnrarOperation *uo = FROM_CAPSULE(file_capsule);
    PartialDataSet *data = uo->unrar_data;
    unsigned int rar_error_code = ERAR_SUCCESS;
    bool has_comment = false;
    Array<wchar> comment;

    ALLOW_THREADS;
    try {
        has_comment = data->Arc.GetComment(&comment);
    } catch(RAR_EXIT err_code) {
        rar_error_code = data->Cmd.DllError == 0 ? RarErrorToDll(err_code) : data->Cmd.DllError;
    } catch (std::bad_alloc&) {
        rar_error_code = ERAR_NO_MEMORY;
    }
    BLOCK_THREADS;
    if (!has_comment) { Py_RETURN_NONE; }
    if (rar_error_code != ERAR_SUCCESS) { convert_rar_error(rar_error_code); return NULL; }
    return wchar_to_unicode(&comment[0], comment.Size());
}

static PyObject*
get_flags(PyObject *self, PyObject *file_capsule) {
    UnrarOperation *uo = FROM_CAPSULE(file_capsule);
    PartialDataSet *data = uo->unrar_data;
    PyObject *ans = PyDict_New();
    if (ans == NULL) return NULL;
#define SET(X) if (PyDict_SetItemString(ans, #X, data->Arc.X ? Py_True : Py_False) != 0) { Py_DECREF(ans); return NULL; }
    SET(Volume);
    SET(Locked);
    SET(Solid);
    SET(NewNumbering);
    SET(Signed);
    SET(Protected);
    SET(Encrypted);
    SET(FirstVolume);
    return ans;
#undef SET
}

static inline unsigned long
combine(unsigned int h, unsigned int l) {
    unsigned long ans = h;
    return (ans << 32) | l;
}


static PyObject*
header_to_python(RARHeaderDataEx *fh, PartialDataSet *data) {
    PyObject *ans = PyDict_New(), *temp, *filename;
    if (!ans) return NULL;
    filename = wchar_to_unicode(fh->FileNameW, wcslen(fh->FileNameW));
    if(!filename) goto error;
#define AVAL(name, code, val) {if (!(temp = Py_BuildValue(code, (val)))) goto error; if (PyDict_SetItemString(ans, name, temp) != 0) goto error; Py_DECREF(temp); temp = NULL;}
    AVAL("filename", "N", filename);
    AVAL("flags", "H", fh->Flags);
    AVAL("pack_size", "k", combine(fh->PackSizeHigh, fh->PackSize));
    AVAL("unpack_size", "k", combine(fh->UnpSizeHigh, fh->UnpSize));
    AVAL("host_os", "b", fh->HostOS);
    AVAL("file_crc", "I", fh->FileCRC);
    AVAL("file_time", "I", fh->FileTime);
    AVAL("unpack_ver", "b", fh->UnpVer);
    AVAL("method", "b", fh->Method);
    AVAL("file_attr", "I", fh->FileAttr);
    AVAL("is_dir", "O", fh->Flags & RHDF_DIRECTORY ? Py_True : Py_False);
    AVAL("is_symlink", "O", (IsLink(fh->FileAttr)) ? Py_True : Py_False);
    // AVAL("atime", "k", combine(fh->AtimeHigh, fh->AtimeLow));
    // AVAL("ctime", "k", combine(fh->CtimeHigh, fh->CtimeLow));
    // AVAL("mtime", "k", combine(fh->MtimeHigh, fh->MtimeLow));
    AVAL("redir_type", "I", fh->RedirType);
    if (fh->RedirNameSize > 0) {
        filename = wchar_to_unicode(fh->RedirName, fh->RedirNameSize);
        if (!filename) goto error;
        AVAL("redir_name", "N", filename);
    }
#undef AVAL
    return ans;
error:
    Py_DECREF(ans);
    return NULL;
}


static PyObject*
read_next_header(PyObject *self, PyObject *file_capsule) {
    UnrarOperation *uo = FROM_CAPSULE(file_capsule);
    PartialDataSet *data = uo->unrar_data;
    RARHeaderDataEx header = {0};  // Cannot be static as it has to be initialized to zero
    ALLOW_THREADS;
    unsigned int retval = RARReadHeaderEx((HANDLE)data, &header);
    BLOCK_THREADS;

    switch(retval) {
        case ERAR_END_ARCHIVE:
            Py_RETURN_NONE;
            break;
        case ERAR_SUCCESS:
            return header_to_python(&header, data);
            break;
        default:
            convert_rar_error(retval);
            break;
    }
    return NULL;
}


static PyObject*
process_file(PyObject *self, PyObject *args) {
    int operation = RAR_TEST;
    PyObject *file_capsule;

    if (!PyArg_ParseTuple(args, "O|i", &file_capsule, &operation)) return NULL;
    UnrarOperation *uo = FROM_CAPSULE(file_capsule);
    PartialDataSet *data = uo->unrar_data;
    ALLOW_THREADS;
    unsigned int retval = RARProcessFile((HANDLE)data, operation, NULL, NULL);
    BLOCK_THREADS;
    if (retval == ERAR_SUCCESS) { Py_RETURN_NONE; }
    convert_rar_error(retval);
    return NULL;
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
        "open_archive(path, callback, mode=RAR_OM_LIST)\n\nOpen the RAR archive at path. By default opens for listing, use mode to change that."
    },

    {"close_archive", (PyCFunction)close_archive, METH_O,
        "close_archive(capsule)\n\nClose the specified archive."
    },

    {"get_comment", (PyCFunction)get_comment, METH_O,
        "get_comment(capsule)\n\nGet the comment from the opened archive capsule which must have been returned by open_archive."
    },

    {"get_flags", (PyCFunction)get_flags, METH_O,
        "get_flags(capsule)\n\nGet the flags from the opened archive capsule which must have been returned by open_archive."
    },

    {"read_next_header", (PyCFunction)read_next_header, METH_O,
        "read_next_header(capsule)\n\nRead the next header from the RAR archive"
    },

    {"process_file", (PyCFunction)process_file, METH_VARARGS,
        "process_file(capsule, operation=RAR_TEST)\n\nProcess the current file. The callback registered in open_archive will be called."
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
    if (PyModule_AddObject(module, "UNRARError", UNRARError) != 0) { INITERROR;}
    if (PyModule_AddIntConstant(module, "RARDllVersion",  RARGetDllVersion()) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_OM_LIST) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_OM_EXTRACT) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_OM_LIST_INCSPLIT) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_SKIP) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_EXTRACT) != 0) { INITERROR; }
    if (PyModule_AddIntMacro(module, RAR_TEST) != 0) { INITERROR; }

#if PY_MAJOR_VERSION >= 3
    return module;
#endif
}
// }}}
