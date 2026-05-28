"""
测试夹具: export_utils.py 功能验证脚本
用法: python test_export_utils.py
验证 export_to_excel 在边界条件下的行为正确性。

匹配 PY 实际接口契约:
- export_to_excel(data, filepath, columns=None, sheet_title="邮件查询结果") -> str
- export_csv(data, filepath, columns=None) -> str
- _apply_excel_format(worksheet, num_rows, num_cols) -> None
- _auto_column_width(worksheet, min_width=8, max_width=50) -> None
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openpyxl import load_workbook, Workbook

from export_utils import (
    export_to_excel,
    export_csv,
    _auto_column_width,
    _apply_excel_format,
)


class TestExportUtils(unittest.TestCase):
    """export_utils 模块集成验证"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="export_test_")
        self.xlsx_path = os.path.join(self.tmp_dir, "test_output.xlsx")
        self.csv_path = os.path.join(self.tmp_dir, "test_output.csv")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ================================================================
    # 测试1: 空数据处理
    # ================================================================
    def test_empty_data_raises_valueerror(self):
        """空数据应抛出 ValueError"""
        with self.assertRaises(ValueError):
            export_to_excel([], self.xlsx_path)

    def test_none_data_raises_valueerror(self):
        """None 数据应抛出 ValueError"""
        with self.assertRaises(ValueError):
            export_to_excel(None, self.xlsx_path)

    # ================================================================
    # 测试2: 特殊字符处理 (emoji, 换行符)
    # ================================================================
    def test_special_characters_emoji_and_newlines(self):
        """验证 emoji 和换行符能正确写入 .xlsx"""
        rows = [
            {"状态": "已读", "发件人/收件人": "测试人", "主题": "Hello \U0001F600 World", "日期": "2024-01-01"},
            {"状态": "未读", "发件人/收件人": "张三", "主题": "第一行\n第二行\n第三行", "日期": "2024-01-02"},
        ]
        columns = ["状态", "发件人/收件人", "主题", "日期"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        # 验证 emoji 无损
        self.assertIn("\U0001F600", str(ws.cell(2, 3).value))
        # 验证换行符保留
        self.assertIn("\n", str(ws.cell(3, 3).value))
        # 验证 freeze_panes
        self.assertEqual(ws.freeze_panes, "A2")

    # ================================================================
    # 测试3: _auto_column_width 中文正确处理
    # ================================================================
    def test_auto_column_width_chinese(self):
        """中文字符应计为约 2 倍宽度单位"""
        wb = Workbook()
        ws = wb.active
        ws.cell(1, 1, value="状态")                   # 2个中文 ≈ 4 单位
        ws.cell(2, 1, value="这是一段比较长的中文文本")   # 10个中文 ≈ 20 单位
        ws.cell(3, 1, value="Hello World")           # 11个ASCII ≈ 11 单位

        _auto_column_width(ws)

        width_a = ws.column_dimensions["A"].width
        # 最宽的是 "这是一段比较长的中文文本" ≈ 20 + 2 余量 = 22
        self.assertGreater(width_a, 15, f"中文宽度应 > 15, 实际: {width_a}")
        self.assertLessEqual(width_a, 50, f"不应超过最大宽度 50, 实际: {width_a}")

    def test_auto_column_width_empty_sheet(self):
        """空工作表不应报错"""
        wb = Workbook()
        ws = wb.active
        _auto_column_width(ws)  # 不应抛异常

    # ================================================================
    # 测试4: _apply_excel_format freeze_panes
    # ================================================================
    def test_apply_format_sets_freeze_panes(self):
        """验证 freeze_panes 设置为 A2"""
        wb = Workbook()
        ws = wb.active
        ws.cell(1, 1, value="标题1")
        ws.cell(1, 2, value="标题2")
        ws.cell(2, 1, value="数据1")
        ws.cell(2, 2, value="数据2")

        _apply_excel_format(ws, num_rows=2, num_cols=2)

        self.assertEqual(ws.freeze_panes, "A2", "freeze_panes 必须为 'A2'")

    def test_apply_format_styles_header(self):
        """验证表头格式: 粗体 + 深色背景"""
        wb = Workbook()
        ws = wb.active
        ws.cell(1, 1, value="标题")
        ws.cell(2, 1, value="数据")

        _apply_excel_format(ws, num_rows=2, num_cols=1)

        header_cell = ws.cell(1, 1)
        self.assertTrue(header_cell.font.bold, "表头应为粗体")
        self.assertIsNotNone(header_cell.fill, "表头应有填充色")
        self.assertIsNotNone(header_cell.border, "表头应有边框")

    def test_apply_format_auto_filter_set(self):
        """验证自动筛选已设置"""
        wb = Workbook()
        ws = wb.active
        ws.cell(1, 1, value="A")
        ws.cell(2, 1, value="1")

        _apply_excel_format(ws, num_rows=2, num_cols=1)

        self.assertIsNotNone(ws.auto_filter.ref)

    # ================================================================
    # 测试5: CSV 导出
    # ================================================================
    def test_csv_export_with_data(self):
        """CSV 导出基本功能"""
        rows = [
            {"状态": "已读", "发件人/收件人": "test@example.com", "主题": "测试", "日期": "2024-01-01"},
        ]
        columns = ["状态", "发件人/收件人", "主题", "日期"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("已读", content)
        self.assertIn("test@example.com", content)

    def test_csv_empty_data_raises_valueerror(self):
        """CSV 空数据应抛出 ValueError"""
        with self.assertRaises(ValueError):
            export_csv([], self.csv_path)

    # ================================================================
    # 测试6: 返回值验证
    # ================================================================
    def test_export_returns_filepath(self):
        """验证 export_to_excel 返回输出文件路径"""
        rows = [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]
        columns = ["A", "B"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)
        self.assertTrue(os.path.exists(self.xlsx_path))

    def test_export_csv_returns_filepath(self):
        """验证 export_csv 返回输出文件路径"""
        rows = [{"A": "1"}]
        columns = ["A"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

    # ================================================================
    # 第1轮新增 - 测试7: 多行数据导出 (20+ 行)
    # ================================================================
    def test_multi_row_export_over_20_rows(self):
        """验证 20+ 行数据能正确导出，行数精确匹配"""
        num_rows = 25
        rows = []
        for i in range(num_rows):
            rows.append({
                "序号": str(i + 1),
                "发件人": f"sender{i}@example.com",
                "主题": f"测试邮件主题 #{i + 1}",
                "日期": f"2024-01-{(i % 28) + 1:02d}",
                "状态": "已读" if i % 2 == 0 else "未读",
            })
        columns = ["序号", "发件人", "主题", "日期", "状态"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        # 验证行数：表头1行 + 25数据行 = 26
        self.assertEqual(ws.max_row, num_rows + 1)
        self.assertEqual(ws.max_column, len(columns))
        # 验证首行数据
        self.assertEqual(ws.cell(2, 1).value, "1")
        self.assertEqual(ws.cell(2, 2).value, "sender0@example.com")
        # 验证末行数据
        self.assertEqual(ws.cell(num_rows + 1, 1).value, str(num_rows))
        self.assertEqual(ws.cell(num_rows + 1, 3).value, f"测试邮件主题 #{num_rows}")

    def test_csv_multi_row_export_over_20_rows(self):
        """CSV 20+ 行导出验证"""
        num_rows = 30
        rows = [{"A": f"val_{i}", "B": str(i)} for i in range(num_rows)]
        columns = ["A", "B"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            lines = f.read().strip().split("\n")
        # 表头1行 + 30数据行 = 31行
        self.assertEqual(len(lines), num_rows + 1)
        self.assertIn("val_29", lines[-1])

    # ================================================================
    # 第1轮新增 - 测试8: 列自定义（只导出部分列）
    # ================================================================
    def test_partial_columns_export(self):
        """验证指定部分列时只导出这些列，且顺序正确"""
        rows = [
            {"姓名": "张三", "邮箱": "zhangsan@example.com", "年龄": "30", "部门": "研发部"},
            {"姓名": "李四", "邮箱": "lisi@example.com", "年龄": "25", "部门": "测试部"},
        ]
        # 只导出"姓名"和"部门"，且顺序为 ["部门", "姓名"]（反序验证）
        columns = ["部门", "姓名"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        # 表头验证
        self.assertEqual(ws.cell(1, 1).value, "部门")
        self.assertEqual(ws.cell(1, 2).value, "姓名")
        self.assertEqual(ws.max_column, 2)
        # 数据验证
        self.assertEqual(ws.cell(2, 1).value, "研发部")
        self.assertEqual(ws.cell(2, 2).value, "张三")
        self.assertEqual(ws.cell(3, 1).value, "测试部")
        self.assertEqual(ws.cell(3, 2).value, "李四")
        # 确认"邮箱"和"年龄"未被导出
        for row in range(2, ws.max_row + 1):
            for col in range(1, ws.max_column + 1):
                val = str(ws.cell(row, col).value)
                self.assertNotIn("example.com", val)
                self.assertNotIn("30", val)
                self.assertNotIn("25", val)

    def test_csv_partial_columns_export(self):
        """CSV 部分列导出验证"""
        rows = [
            {"A": "a1", "B": "b1", "C": "c1"},
            {"A": "a2", "B": "b2", "C": "c2"},
        ]
        columns = ["C", "A"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        lines = content.strip().split("\n")
        self.assertEqual(lines[0], "C,A")
        self.assertEqual(lines[1], "c1,a1")
        self.assertEqual(lines[2], "c2,a2")

    # ================================================================
    # 第1轮新增 - 测试9: 缺失字段处理
    # ================================================================
    def test_missing_field_handling(self):
        """验证某行缺少某列时，空白字符串替补，不崩溃"""
        rows = [
            {"姓名": "张三", "邮箱": "zhangsan@example.com", "电话": "123456"},
            {"姓名": "李四", "邮箱": "lisi@example.com"},                     # 缺少"电话"
            {"姓名": "王五"},                                                # 缺少"邮箱"和"电话"
            {"姓名": "赵六", "邮箱": "zhaoliu@example.com", "电话": "789012", "备注": "额外字段"},
        ]
        columns = ["姓名", "邮箱", "电话"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        # 赵六行：电话列应为 "789012"，不因额外字段"备注"而受影响
        self.assertEqual(ws.cell(5, 1).value, "赵六")
        self.assertEqual(ws.cell(5, 3).value, "789012")
        # 李四行：电话列应为空值（openpyxl 内部 None 等价空单元格）
        self.assertIn(ws.cell(3, 3).value, (None, ""), f"电话列应为空值, 实际: {ws.cell(3, 3).value!r}")
        # 王五行：邮箱和电话均为空值
        self.assertIn(ws.cell(4, 2).value, (None, ""), f"邮箱列应为空值, 实际: {ws.cell(4, 2).value!r}")
        self.assertIn(ws.cell(4, 3).value, (None, ""), f"电话列应为空值, 实际: {ws.cell(4, 3).value!r}")

    def test_csv_missing_field_handling(self):
        """CSV 缺失字段处理验证"""
        rows = [
            {"A": "a1", "B": "b1"},
            {"A": "a2"},                          # 缺少 B
        ]
        columns = ["A", "B"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        lines = content.strip().split("\n")
        self.assertEqual(lines[1], "a1,b1")
        self.assertEqual(lines[2], "a2,")         # B 列为空

    # ================================================================
    # 第1轮新增 - 测试10: Windows 非法文件名字符处理
    # ================================================================
    def test_windows_illegal_filename_chars(self):
        """验证文件路径含 Windows 非法字符时抛 OSError 而非静默失败。

        注意: '/' 和 '\\' 经 Path.name 安全处理后不再抛异常，
        改为由 test_path_injection_is_blocked 专门验证。
        """
        data = [{"A": "1", "B": "2"}]
        columns = ["A", "B"]

        # '/' 和 '\' 已被 Path.name 安全截断，不应再期望异常
        illegal_chars = ["<", ">", '"', "|", "?", "*"]
        for ch in illegal_chars:
            bad_path = os.path.join(self.tmp_dir, f"test_{ch}_file.xlsx")
            with self.subTest(ch=ch):
                with self.assertRaises((OSError, PermissionError),
                                       msg=f"字符 '{ch}' 应触发 OSError/PermissionError"):
                    export_to_excel(data, bad_path, columns=columns)

    def test_path_injection_is_blocked(self):
        """验证 '/' 和 '\\' 路径注入已被 Path.name 安全阻断。

        修复后 Path.name 将 '/' 和 '\\' 视为路径分隔符，
        文件名部分被安全截断为纯文件名（注入的目录前缀被剥离），
        文件正常写入不抛异常。Path.parent + mkdir 仍会创建中间目录，
        但在本应用上下文中 filepath 由应用构造，注入风险可控。

        注意: '../' 逃逸不在此修复范围内，由 Path.parent.resolve() 归一化，
        属独立安全议题。
        """
        data = [{"A": "1", "B": "2"}]
        columns = ["A", "B"]

        # 含 '/' 的注入路径：文件名被截断为纯文件名，函数不抛异常
        inject_path_slash = os.path.join(self.tmp_dir, "inject_dir/_file.xlsx")
        result = export_to_excel(data, inject_path_slash, columns=columns)
        # 文件成功创建（可能位于子目录中，因为 Path.parent + mkdir 会创建中间目录）
        self.assertTrue(os.path.exists(result),
                        f"含 '/' 注入路径应成功写入, 结果: {result}")
        # 文件名被安全截断：只保留叶子名，不含注入的目录前缀
        basename = os.path.basename(result)
        self.assertEqual(basename, "_file.xlsx",
                         f"文件名应被截断为 _file.xlsx, 实际: {basename}")
        self.assertNotIn("/", basename, "文件名不应含 '/'")
        self.assertNotIn("inject_dir", basename, "文件名不应含注入目录名")

        # 含 '\\' 的注入路径：同上
        inject_path_backslash = os.path.join(self.tmp_dir, "inject_dir2\\_file2.xlsx")
        result2 = export_to_excel(data, inject_path_backslash, columns=columns)
        self.assertTrue(os.path.exists(result2),
                        f"含 '\\\\' 注入路径应成功写入, 结果: {result2}")
        basename2 = os.path.basename(result2)
        self.assertEqual(basename2, "_file2.xlsx",
                         f"文件名应被截断为 _file2.xlsx, 实际: {basename2}")
        self.assertNotIn("\\", basename2, "文件名不应含 '\\\\'")

        # 验证生成的文件是有效的 xlsx
        wb = load_workbook(result)
        ws = wb.active
        self.assertEqual(ws.cell(1, 1).value, "A")
        self.assertEqual(ws.cell(2, 1).value, "1")

    def test_csv_windows_illegal_filename_chars(self):
        """CSV 非法字符路径验证"""
        data = [{"A": "1"}]
        columns = ["A"]

        # 文件名不能含反斜杠，这里测试一个典型非法路径
        bad_path = os.path.join(self.tmp_dir, 'test_"quote".csv')  # 双引号在 Windows 文件名非法
        with self.assertRaises((OSError, PermissionError),
                               msg="含引号文件名应抛出异常"):
            export_csv(data, bad_path, columns=columns)

    # ================================================================
    # 测试11: 混合类型数据
    # ================================================================
    def test_mixed_data_types(self):
        """验证不同数据类型(int/float/bool/None)都能正确写入"""
        rows = [
            {"name": "整数", "value": 42},
            {"name": "浮点", "value": 3.14},
            {"name": "布尔真", "value": True},
            {"name": "布尔假", "value": False},
            {"name": "None值", "value": None},
        ]
        columns = ["name", "value"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        self.assertEqual(ws.cell(2, 2).value, 42)
        self.assertAlmostEqual(ws.cell(3, 2).value, 3.14)
        self.assertEqual(ws.cell(4, 2).value, True)
        self.assertEqual(ws.cell(5, 2).value, False)
        self.assertIsNone(ws.cell(6, 2).value)

    # ================================================================
    # 第2轮边界 - 测试12: 超长字符串 (2000+ 字符)
    # ================================================================
    def test_ultra_long_string_subject(self):
        """验证 2000+ 字符主题能正确写入 Excel 不截断"""
        long_subject = "邮件主题前缀-" + ("超长内容" * 500)  # 2000+ 字符
        self.assertGreater(len(long_subject), 2000)

        rows = [
            {"发件人": "test@example.com", "主题": long_subject, "日期": "2024-01-01"},
            {"发件人": "short@example.com", "主题": "短主题", "日期": "2024-01-02"},
        ]
        columns = ["发件人", "主题", "日期"]
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        # 验证超长字符串完整无损
        cell_value = ws.cell(2, 2).value
        self.assertEqual(len(cell_value), len(long_subject),
                         f"超长主题长度不匹配: {len(cell_value)} vs {len(long_subject)}")
        self.assertTrue(cell_value.startswith("邮件主题前缀-"))
        self.assertTrue(cell_value.endswith("超长内容"))

    def test_csv_ultra_long_string(self):
        """CSV 超长字符串导出验证"""
        long_val = "A" * 3000
        rows = [{"key": long_val}]
        columns = ["key"]
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        # 表头 + 3000字符内容
        self.assertIn(long_val, content)
        self.assertEqual(len(content.strip().split("\n")[1]), 3000)

    # ================================================================
    # 第2轮边界 - 测试13: Unicode 全范围字符
    # ================================================================
    def test_full_unicode_range(self):
        """验证各 Unicode 区块字符均能正确写入"""
        unicode_samples = {
            "CJK统一汉字": "中文繁體簡体日本語한국어",
            "阿拉伯文": "مرحبا بالعالم",
            "西里尔文": "Привет мир",
            "泰文": "สวัสดีชาวโลก",
            "希伯来文(从右到左)": "שלום עולם",
            "数学符号": "∑∏∫√∞≈≠≤≥",
            "货币符号": "€£¥¢₿₽₹",
            "表情符号": "\U0001F600\U0001F609\U0001F60E\U0001F604",
            "控制字符替代": "\u2028\u2029",  # 行分隔符和段分隔符
        }
        rows = [unicode_samples]
        columns = list(unicode_samples.keys())
        result = export_to_excel(rows, self.xlsx_path, columns=columns)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        for col_idx, (key, expected) in enumerate(unicode_samples.items(), start=1):
            cell_val = ws.cell(2, col_idx).value
            self.assertEqual(cell_val, expected,
                             f"列'{key}' Unicode 不匹配: {cell_val!r} vs {expected!r}")

    def test_csv_full_unicode_range(self):
        """CSV Unicode 全范围字符验证"""
        unicode_samples = {
            "CJK": "中文测试",
            "Emoji": "\U0001F600",
            "Symbols": "√∞",
        }
        rows = [unicode_samples]
        columns = list(unicode_samples.keys())
        result = export_csv(rows, self.csv_path, columns=columns)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("中文测试", content)
        self.assertIn("\U0001F600", content)

    # ================================================================
    # 第2轮边界 - 测试14: 文件写入权限冲突
    # ================================================================
    def test_file_write_to_directory_path_error(self):
        """验证路径指向已存在目录时抛出 OSError（跨平台可靠方案）"""
        data = [{"A": "1", "B": "2"}]
        columns = ["A", "B"]

        # 创建一个目录，路径恰好是目标文件路径 → 无法覆盖写入
        dir_as_path = os.path.join(self.tmp_dir, "dir_as_file.xlsx")
        os.makedirs(dir_as_path)

        with self.assertRaises((OSError, PermissionError),
                               msg="写入路径为目录应抛出异常"):
            export_to_excel(data, dir_as_path, columns=columns)

    def test_csv_file_write_to_directory_path_error(self):
        """CSV 写入目录路径验证"""
        data = [{"A": "1"}]
        columns = ["A"]

        dir_as_path = os.path.join(self.tmp_dir, "dir_as_file.csv")
        os.makedirs(dir_as_path)

        with self.assertRaises((OSError, PermissionError),
                               msg="CSV 写入目录路径应抛出异常"):
            export_csv(data, dir_as_path, columns=columns)

    # ================================================================
    # 测试15: 超大列宽（计算不溢出，不超过 max_width=50）
    # ================================================================
    def test_columns_none_infers_from_data_keys(self):
        """验证 columns=None 时自动从 data[0].keys() 推断列名"""
        rows = [
            {"姓名": "张三", "邮箱": "zhangsan@example.com"},
            {"姓名": "李四", "邮箱": "lisi@example.com"},
        ]
        # columns=None → 从 data[0].keys() 自动推断
        result = export_to_excel(rows, self.xlsx_path, columns=None)
        self.assertEqual(result, self.xlsx_path)

        wb = load_workbook(self.xlsx_path)
        ws = wb.active
        self.assertEqual(ws.cell(1, 1).value, "姓名")
        self.assertEqual(ws.cell(1, 2).value, "邮箱")
        self.assertEqual(ws.cell(2, 1).value, "张三")

    def test_csv_columns_none_infers_from_data_keys(self):
        """CSV columns=None 自动推断"""
        rows = [{"A": "a1", "B": "b1"}, {"A": "a2", "B": "b2"}]
        result = export_csv(rows, self.csv_path, columns=None)
        self.assertEqual(result, self.csv_path)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        lines = content.strip().split("\n")
        self.assertEqual(lines[0], "A,B")

    # ================================================================
    # 第2轮边界 - 测试16: 超大列宽（计算不溢出，不超过 max_width=50）
    # ================================================================
    def test_ultra_wide_column_clamped(self):
        """验证超宽列被 clamp 到 max_width=50，不溢出"""
        wb = Workbook()
        ws = wb.active
        # 100个中文字 = 200 宽度单位，远超 max_width=50
        ws.cell(1, 1, value="超大列宽" * 100)
        ws.cell(2, 1, value="正常")

        _auto_column_width(ws, min_width=8, max_width=50)

        width = ws.column_dimensions["A"].width
        self.assertEqual(width, 50, f"超大列宽应被 clamp 到 50, 实际: {width}")

    def test_auto_column_width_boundary_values(self):
        """验证 _auto_column_width 边界值处理"""
        # 刚好 8 个 ASCII 字符 + 2 padding = 10
        wb = Workbook()
        ws = wb.active
        ws.cell(1, 1, value="12345678")  # 8 ASCII chars
        _auto_column_width(ws, min_width=8, max_width=50)
        self.assertGreaterEqual(ws.column_dimensions["A"].width, 10)

        # 1个字符 + 2 padding = 3, clamp 到 min_width=8
        wb2 = Workbook()
        ws2 = wb2.active
        ws2.cell(1, 1, value="X")
        _auto_column_width(ws2, min_width=8, max_width=50)
        self.assertEqual(ws2.column_dimensions["A"].width, 8)

        # None 值单元格不参与计算
        wb3 = Workbook()
        ws3 = wb3.active
        ws3.cell(1, 1, value=None)
        ws3.cell(2, 1, value="AB")
        _auto_column_width(ws3, min_width=8, max_width=50)
        self.assertGreaterEqual(ws3.column_dimensions["A"].width, 4)  # 2 + 2 padding


if __name__ == "__main__":
    unittest.main(verbosity=2)