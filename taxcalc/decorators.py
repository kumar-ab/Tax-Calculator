import numpy as np
import pandas as pd
import inspect
from numba import jit, vectorize, guvectorize
from functools import wraps
from six import StringIO


def dataframe_guvectorize(dtype_args, dtype_sig):
    """
    Extracts numpy arrays from caller arguments and passes them
    to guvectorized numba functions
    """
    def make_wrapper(func):
        vecd_f = guvectorize(dtype_args, dtype_sig)(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            # np_arrays = [getattr(args[0], i).values for i in theargs]
            arrays = [arg.values for arg in args]
            ans = vecd_f(*arrays)
            return ans
        return wrapper
    return make_wrapper


def dataframe_vectorize(dtype_args):
    """
    Extracts numpy arrays from caller arguments and passes them
    to vectorized numba functions
    """
    def make_wrapper(func):
        vecd_f = vectorize(dtype_args)(func)

        @wraps(func)
        def wrapper(*args, **kwargs):
            arrays = [arg.values for arg in args]
            ans = vecd_f(*arrays)
            return ans
        return wrapper
    return make_wrapper


def dataframe_wrap_guvectorize(dtype_args, dtype_sig):
    """
    Extracts particular numpy arrays from caller argments and passes
    them to guvectorize. Goes one step further than dataframe_guvectorize
    by looking for the column names in the dataframe and just extracting those
    """
    def make_wrapper(func):
        theargs = inspect.getargspec(func).args
        vecd_f = guvectorize(dtype_args, dtype_sig)(func)

        def wrapper(*args, **kwargs):
            np_arrays = [getattr(args[0], i).values for i in theargs]
            ans = vecd_f(*np_arrays)
            return ans
        return wrapper
    return make_wrapper


def create_apply_function_string(sigout, sigin, parameters):
    """
    Create a string for a function of the form:
        def ap_fuc(x_0, x_1, x_2, ...):
            for i in range(len(x_0)):
                 x_0[i], ... = jitted_f(x_j[i],....)
            return x_0[i], ...
    where the specific args to jitted_f and the number of
    values to return is destermined by sigout and signn

    Parameters
    ----------
    sigout: iterable of the out arguments

    sigin: iterable of the in arguments

    parameters: iterable of which of the args (from in_args) are parameter
                variables (as opposed to column records). This influences
                how we construct the '_apply' function

    Returns
    -------
    a String representing the function
    """


    s = StringIO()
    total_len = len(sigout) + len(sigin)
    out_args = ["x_" + str(i) for i in range(0, len(sigout))]
    in_args = ["x_" + str(i) for i in range(len(sigout), total_len)]

    s.write("def ap_func({0}):\n".format(",".join(out_args + in_args)))
    s.write("  for i in range(len(x_0)):\n")

    out_index = [x + "[i]" for x in out_args]
    in_index = []
    for arg, _var in zip(in_args, sigin):
        in_index.append(arg + "[i]" if _var not in parameters else arg)
    s.write("    " + ",".join(out_index) + " = ")
    s.write("jitted_f(" + ",".join(in_index) + ")\n")
    s.write("  return " + ",".join(out_args) + "\n")

    return s.getvalue()


def create_toplevel_function_string(args_out, args_in, pm_or_pf):
    """
    Create a string for a function of the form:
        def hl_func(x_0, x_1, x_2, ...):
            outputs = (...) = calc_func(...)

            header = [...]
            return DataFrame(data, columns=header)
            
    where the specific args to jitted_f and the number of
    values to return is destermined by sigout and signn

    Parameters
    ----------
    sigout: iterable of the out arguments

    sigin: iterable of the in arguments

    Returns
    -------
    a String representing the function
    """

    s = StringIO()

    s.write("def hl_func(pm, pf):\n")
    s.write("    from pandas import DataFrame\n")
    s.write("    import numpy as np\n")
    s.write("    outputs = \\\n")
    outs = []
    for p, attr in zip(pm_or_pf, args_out + args_in):
        outs.append(p + "." + attr + ", ")
    outs = [m_or_f + "." + arg for m_or_f, arg in zip(pm_or_pf, args_out)]
    s.write("        (" + ", ".join(outs) + ") = \\\n")
    s.write("        " + "applied_f(")
    for p, attr in zip(pm_or_pf, args_out + args_in):
        s.write(p + "." + attr + ", ")
    s.write(")\n")

    s.write("    header = [")
    col_headers = ["'" + out + "'" for out in args_out]
    s.write(", ".join(col_headers))
    s.write("]\n")
    if len(args_out) == 1:
        s.write("    return DataFrame(data=outputs,"
                "columns=header)")
    else:
        s.write("    return DataFrame(data=np.column_stack("
                "outputs),columns=header)")

    return s.getvalue()


def make_apply_function(func, out_args, in_args, parameters, do_jit=True,
                        **kwargs):
    """
    Takes a '_calc' function and creates the necessary Python code for an
    _apply style function. Will also jit the function if desired

    Parameters
    ----------
    func: the 'calc' style function

    out_args: list of out arguments for the apply function

    in_args: list of in arguments for the apply function

    parameters: iterable of which of the args (from in_args) are parameter
                variables (as opposed to column records). This influences
                how we construct the '_apply' function

    do_jit: Bool, if True, jit the resulting apply function

    Returns
    -------
    '_apply' style function

    """
    jitted_f = jit(**kwargs)(func)
    apfunc = create_apply_function_string(out_args, in_args, parameters)
    func_code = compile(apfunc, "<string>", "exec")
    fakeglobals = {}
    eval(func_code, {"jitted_f": jitted_f}, fakeglobals)
    if do_jit:
        return jit(**kwargs)(fakeglobals['ap_func'])
    else:
        return fakeglobals['ap_func']


def apply_jit(dtype_sig_out, dtype_sig_in, parameters=None, **kwargs):
    """
    make a decorator that takes in a _calc-style function, handle
    the apply step
    """
    if not parameters:
        parameters = []
    def make_wrapper(func):
        theargs = inspect.getargspec(func).args
        jitted_f = jit(**kwargs)(func)

        jitted_apply = make_apply_function(func, dtype_sig_out,
                                           dtype_sig_in, parameters,
                                           **kwargs)

        def wrapper(*args, **kwargs):
            in_arrays = []
            out_arrays = []
            for farg in theargs:
                if hasattr(args[0], farg):
                    in_arrays.append(getattr(args[0], farg))
                else:
                    in_arrays.append(getattr(args[1], farg))

            for farg in dtype_sig_out:
                if hasattr(args[0], farg):
                    out_arrays.append(getattr(args[0], farg))
                else:
                    out_arrays.append(getattr(args[1], farg))

            final_array = out_arrays + in_arrays
            ans = jitted_apply(*final_array)
            return ans

        return wrapper
    return make_wrapper


def iterate_jit(parameters=None, **kwargs):
    """
    make a decorator that takes in a _calc-style function, create a
    function that handles the "high-level" function and the "_apply"
    style function
    """
    if not parameters:
        parameters = []
    def make_wrapper(func):

        # Step 1. Wrap this function in apply_jit
        # from apply_jit

        # Get the input arguments from the function
        in_args = inspect.getargspec(func).args
        src = inspect.getsourcelines(func)[0]


        # Discover the return arguments from the function
        # through parsing source - BOO!
        return_line = None
        begin_idx = None
        all_returned_vals = []
        for line in reversed(src):
            idx = line.rfind('return')
            all_returned_vals.append(line)
            if (idx >= 0):
                return_line = line
                break

        if return_line is not None:
            #edit out 'return'
            all_returned_vals[-1] = return_line[idx+6:].strip(" ,()\n")
            all_out_args = []
            for ret_line in all_returned_vals:
                out_args = [out.strip() for out in
                        ret_line.strip(" ,()\n").split(",")]
                out_args.extend(all_out_args)
                all_out_args = out_args

        else:
            raise ValueError("Can't find return statement in function!")

        # Now create the apply jitted function
        applied_jitted_f = make_apply_function(func,
                                               list(reversed(all_out_args)),
                                               in_args,
                                               parameters=parameters,
                                               do_jit=True, **kwargs)

        def wrapper(*args, **kwargs):
            in_arrays = []
            out_arrays = []
            pm_or_pf = []
            for farg in all_out_args + in_args:
                if hasattr(args[0], farg):
                    in_arrays.append(getattr(args[0], farg))
                    pm_or_pf.append("pm")
                else:
                    in_arrays.append(getattr(args[1], farg))
                    pm_or_pf.append("pf")

            # Create the high level function
            high_level_func = create_toplevel_function_string(all_out_args,
                                                              in_args,
                                                              pm_or_pf)
            func_code = compile(high_level_func, "<string>", "exec")
            fakeglobals = {}
            eval(func_code, {"applied_f": applied_jitted_f}, fakeglobals)
            high_level_fn = fakeglobals['hl_func']

            ans = high_level_fn(*args, **kwargs)
            return ans

        return wrapper

    return make_wrapper
