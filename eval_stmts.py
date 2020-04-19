# Modified version of https://gist.github.com/nitros12/2c3c265813121492655bc95aa54da6b9
import ast


def insert_returns(body):
    # insert return stmt if the last expression is a expression statement
    if isinstance(body[-1], ast.Expr):
        body[-1] = ast.Return(body[-1].value)
        ast.fix_missing_locations(body[-1])

    # for if statements, we insert returns into the body and the orelse
    if isinstance(body[-1], ast.If):
        insert_returns(body[-1].body)
        insert_returns(body[-1].orelse)

    # for with blocks, again we insert returns into the body
    if isinstance(body[-1], ast.With):
        insert_returns(body[-1].body)

    # for with blocks, again we insert returns into the body
    if isinstance(body[-1], ast.AsyncWith):
        insert_returns(body[-1].body)


async def eval_stmts(stmts, env=None):
    """
    Evaluates input.
    If the last statement is an expression, that is the return value.

    >>> from asyncio import run
    >>> run(eval_stmts("1+1"))
    2

    >>> ctx = {}
    >>> run(eval_stmts("ctx['foo'] = 1", {"ctx": ctx}))
    >>> ctx['foo']
    1

    >>> run(eval_stmts('''
    ... async def f():
    ...    return 42
    ...
    ... await f()'''))
    42
    """

    parsed_stmts = ast.parse(stmts)

    fn_name = "_eval_expr"

    fn = f"async def {fn_name}(): pass"
    parsed_fn = ast.parse(fn)

    for node in parsed_stmts.body:
        ast.increment_lineno(node)

    insert_returns(parsed_stmts.body)

    parsed_fn.body[0].body = parsed_stmts.body
    exec(compile(parsed_fn, filename="<ast>", mode="exec"), env)

    return await eval(f"{fn_name}()", env)


if __name__ == "__main__":
    import doctest

    doctest.testmod()
