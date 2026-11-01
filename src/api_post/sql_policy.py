"""MySQL statement selection and conservative first-version execution policy."""
import logging
import sqlglot
from sqlglot import exp, ErrorLevel
from sqlglot.tokens import TokenType

# Parser fallback warnings can include submitted SQL. Keep them out of application logs.
_sql_logger = logging.getLogger("sqlglot")
_sql_logger.addHandler(logging.NullHandler())
_sql_logger.propagate = False


def selected_statement(text, start=0, end=0):
    if not 0 <= start <= end <= len(text):
        raise ValueError("SQL选区不合法")
    if end > start:
        return text[start:end].strip()
    tokens = sqlglot.Dialect.get_or_raise("mysql").tokenize(text)
    boundaries = [t.start for t in tokens if t.token_type == TokenType.SEMICOLON]
    left = max([i + 1 for i in boundaries if i < start] or [0])
    right = min([i for i in boundaries if i >= start] or [len(text)])
    value = text[left:right].strip()
    if not value and left > 0:
        previous = max([i + 1 for i in boundaries if i < left - 1] or [0])
        value = text[previous:left - 1].strip()
    return value


def parse_statement(text):
    if not text.strip() or len(text.encode()) > 128 * 1024:
        raise ValueError("请选择一条SQL，长度最多128 KiB")
    if any(x in text for x in ("/*!", "/*+", "\x00")):
        raise ValueError("第一版不支持执行指令注释或优化器提示")
    try:
        trees = [x for x in sqlglot.parse(text, read="mysql", error_level=ErrorLevel.RAISE) if x]
    except Exception:
        raise ValueError("无法安全解析此SQL，请检查MySQL语法") from None
    if len(trees) != 1:
        raise ValueError("每次仅运行选中的一条SQL；请缩小选区")
    tree = trees[0]
    for node in tree.walk():
        if isinstance(node, (exp.Into, exp.Lock, exp.Parameter, exp.SessionParameter, exp.PropertyEQ)):
            raise ValueError("不支持文件导出、锁定查询、变量或会话赋值")
        if isinstance(node, exp.Anonymous) and node.name.upper() not in {
            "DATABASE", "VERSION", "IFNULL", "IF", "DATE_FORMAT", "STR_TO_DATE", "TIMESTAMPDIFF",
            "JSON_EXTRACT", "JSON_UNQUOTE", "JSON_CONTAINS", "FIND_IN_SET", "GROUP_CONCAT",
        }:
            raise ValueError("第一版不执行自定义函数或此类特殊函数")
        if isinstance(node, exp.Func) and node.sql_name().upper() in {
            "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "UUID_SHORT",
        }:
            raise ValueError("不允许有副作用或消耗资源的特殊函数")
    if isinstance(tree, (exp.Select, exp.SetOperation)):
        kind = "query"
    elif isinstance(tree, exp.Show) and tree.this in {"DATABASES", "TABLES", "COLUMNS", "INDEX", "CREATE TABLE", "TABLE STATUS"}:
        kind = "query"
    elif isinstance(tree, exp.Describe):
        if tree.args.get("style") or tree.args.get("kind") or not isinstance(tree.this, (exp.Table, exp.Select, exp.SetOperation)):
            raise ValueError("不支持EXPLAIN ANALYZE或此解释形式")
        kind = "query"
    elif isinstance(tree, exp.Create):
        schema=tree.this
        if tree.args.get('kind')!='TABLE' or not isinstance(schema,exp.Schema) or not isinstance(schema.this,exp.Table):
            raise ValueError('仅支持明确列定义的CREATE TABLE，不支持视图、LIKE或AS SELECT')
        if any(v for k,v in tree.args.items() if k not in {'this','kind','exists','properties','indexes'}):
            raise ValueError('不支持替换、临时表或其他扩展建表方式')
        if not schema.expressions or len(schema.expressions)>1000 or not any(isinstance(c,exp.ColumnDef) for c in schema.expressions):
            raise ValueError('建表须定义字段，字段和约束合计最多1000项')
        if any(isinstance(n,(exp.Select,exp.SetOperation,exp.Subquery)) for n in tree.walk()):
            raise ValueError('建表不支持嵌套查询')
        properties=tree.args.get('properties')
        for prop in properties.expressions if properties else []:
            if not isinstance(prop,(exp.EngineProperty,exp.CharacterSetProperty,exp.CollateProperty,exp.SchemaCommentProperty)):
                raise ValueError('建表仅支持InnoDB引擎、字符集、排序规则和表注释选项')
            if isinstance(prop,exp.EngineProperty) and prop.this.name.lower()!='innodb':
                raise ValueError('建表仅支持InnoDB引擎')
        if not any(isinstance(prop,exp.EngineProperty) for prop in (properties.expressions if properties else [])):
            tree.set('properties',exp.Properties(expressions=[*(properties.expressions if properties else []),exp.EngineProperty(this=exp.Var(this='InnoDB'))]))
        kind = 'create'
    elif isinstance(tree, (exp.Update, exp.Delete)):
        table = tree.this
        if not isinstance(table, exp.Table) or table.args.get("joins") or table.args.get("alias") or table.catalog:
            raise ValueError("第一版仅支持无别名、无JOIN的单表更新和删除")
        if not tree.args.get("where") or tree.args.get("with_") or tree.args.get("tables") or tree.args.get("using") or tree.args.get("limit") or tree.args.get("order") or tree.args.get("returning"):
            raise ValueError("更新/删除须有WHERE；第一版不支持多表、LIMIT或ORDER BY")
        if any(isinstance(n, (exp.Select, exp.SetOperation, exp.Subquery, exp.Func)) for n in tree.walk()):
            raise ValueError("更新/删除第一版不支持子查询或函数表达式")
        kind = "write"
    else:
        raise ValueError("仅支持查询、EXPLAIN、CREATE TABLE和有WHERE的单表UPDATE/DELETE；不支持其他DDL、INSERT或事务控制")
    for node in tree.walk():
        if node is not tree and isinstance(node, (exp.Update, exp.Delete, exp.Insert, exp.Command)):
            raise ValueError("不允许嵌套写入或命令")
    return tree, kind


def quote(name):
    if not name or len(name) > 64 or any(ord(c) < 32 for c in name):
        raise ValueError("库/表/字段名称不合法")
    return "`" + name.replace("`", "``") + "`"
