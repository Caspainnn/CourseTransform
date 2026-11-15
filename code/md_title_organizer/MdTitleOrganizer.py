import re
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from enum import Enum


class TitleLevel(Enum):
    """标题级别枚举"""
    LEVEL_1 = (1, "#")      # 章
    LEVEL_2 = (2, "##")     # 节
    LEVEL_3 = (3, "###")    # 小节
    LEVEL_4 = (4, "####")   # 知识点


class TitlePattern:
    """标题识别模式"""

    # 日期/时间型文本：绝不能当作标题
    DATE_PATTERN = r'^(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2})\s*(.*)$'

    # 数值型正文噪声：如 "7999 0.5299999713897705 0.5799999833106995"
    # 这类通常是训练输出统计信息，不是标题
    NUMERIC_TEXT_PATTERN = r'^(?:\d+\s+){2,}\d+(?:\.\d+)?$|^(?:\d+\s+){1,}\d+(?:\.\d+)?$'

    # 纯编号型列表文本：例如 “（4） 设备配置” / “(4) 设备配置” / “（一） 编号说明”
    # 这类属于正文编号，不应升格为标题。
    PAREN_LIST_PATTERN = r'^[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[）)]\s+.+$'

    # 一级标题模式（章、部分）
    CHAPTER_PATTERN = r'^(第\s*[0-9零一二三四五六七八九十百千万]+\s*章)\s*(.*)$'
    CHAPTER_EN_PATTERN = r'^(Chapter\s+[0-9IVX]+)\s+(.*)$'
    PART_PATTERN = r'^(Part\s+[0-9IVX]+)\s+(.*)$'
    
    # 二级标题模式（节、单元、模块）
    SECTION_PATTERN = r'^(第\s*[0-9零一二三四五六七八九十百千万]+\s*节)\s*(.*)$'
    UNIT_PATTERN = r'^(第\s*[0-9零一二三四五六七八九十百千万]+\s*单元)\s*(.*)$'
    DECIMAL_2_PATTERN = r'^(\d+\.\d+)\s+(.*)$'
    SECTION_EN_PATTERN = r'^(Section\s+\d+)\s+(.*)$'
    LESSON_PATTERN = r'^(Lesson\s+\d+)\s+(.*)$'
    MODULE_PATTERN = r'^(Module\s+\d+)\s+(.*)$'
    CHINESE_NUM_2_PATTERN = r'^([一二三四五六七八九十]+、)\s*(.*)$'
    
    # 三级标题模式（小节）
    DECIMAL_3_PATTERN = r'^(\d+\.\d+\.\d+)\s+(.*)$'
    CHINESE_PAREN_PATTERN = r'^((（[一二三四五六七八九十]+）)|(\([^)]*\)))\s*(.*)$'
    LETTER_PATTERN = r'^([A-Z]\.)\s+(.*)$'
    
    # 四级标题模式（知识点）
    DECIMAL_4_PATTERN = r'^(\d+\.\d+\.\d+\.\d+)\s+(.*)$'
    PAREN_NUM_PATTERN = r'^((（\d+）)|(\(\d+\)))\s+(.*)$'
    SINGLE_NUM_PATTERN = r'^(\d+)\s+(.*)$'  # 单个数字，表示章
    
    # 特殊模块
    SUMMARY_PATTERN = r'^(本章小结|小结)$'
    PRACTICE_PATTERN = r'^(课后習題|課後習題|课后习题|思考题)$'
    REFERENCES_PATTERN = r'^(参考文献|參考文獻)$'
    APPENDIX_PATTERN = r'^(附录|附錄)\s*([0-9A-Z]*)$'
    TOC_PATTERN = r'^(目录|目錄|Contents)$'


class MdTitleOrganizer:
    """MD文档标题处理器"""
    
    def __init__(self):
        """初始化标题处理器"""
        self.patterns = self._compile_patterns()
        self.title_context: List[Dict] = []  # 用于追踪标题上下文
    
    @staticmethod
    def _compile_patterns() -> Dict[str, Tuple[str, TitleLevel]]:
        """编译所有标题识别模式"""
        return {
            # 一级标题
            "chapter": (TitlePattern.CHAPTER_PATTERN, TitleLevel.LEVEL_1),
            "chapter_en": (TitlePattern.CHAPTER_EN_PATTERN, TitleLevel.LEVEL_1),
            "part": (TitlePattern.PART_PATTERN, TitleLevel.LEVEL_1),
            "appendix": (TitlePattern.APPENDIX_PATTERN, TitleLevel.LEVEL_1),
            "references": (TitlePattern.REFERENCES_PATTERN, TitleLevel.LEVEL_1),
            "single_num": (TitlePattern.SINGLE_NUM_PATTERN, TitleLevel.LEVEL_1),
            
            # 二级标题
            "section": (TitlePattern.SECTION_PATTERN, TitleLevel.LEVEL_2),
            "unit": (TitlePattern.UNIT_PATTERN, TitleLevel.LEVEL_2),
            "decimal_2": (TitlePattern.DECIMAL_2_PATTERN, TitleLevel.LEVEL_2),
            "section_en": (TitlePattern.SECTION_EN_PATTERN, TitleLevel.LEVEL_2),
            "lesson": (TitlePattern.LESSON_PATTERN, TitleLevel.LEVEL_2),
            "module": (TitlePattern.MODULE_PATTERN, TitleLevel.LEVEL_2),
            "chinese_num_2": (TitlePattern.CHINESE_NUM_2_PATTERN, TitleLevel.LEVEL_2),
            "summary_full": (TitlePattern.SUMMARY_PATTERN, TitleLevel.LEVEL_2),
            "practice": (TitlePattern.PRACTICE_PATTERN, TitleLevel.LEVEL_2),
            
            # 三级标题
            "decimal_3": (TitlePattern.DECIMAL_3_PATTERN, TitleLevel.LEVEL_3),
            "chinese_paren": (TitlePattern.CHINESE_PAREN_PATTERN, TitleLevel.LEVEL_3),
            "letter": (TitlePattern.LETTER_PATTERN, TitleLevel.LEVEL_3),
            
            # 四级标题
            "decimal_4": (TitlePattern.DECIMAL_4_PATTERN, TitleLevel.LEVEL_4),
            "paren_num": (TitlePattern.PAREN_NUM_PATTERN, TitleLevel.LEVEL_4),
        }
    
    def _detect_title_level(self, line: str) -> Optional[Tuple[TitleLevel, str]]:
        """
        检测行是否为标题以及其级别
        返回 (级别, 原始标题文本) 或 None
        """
        original_line = line.strip()

        # 检查是否已经是markdown标题，如果是则提取文本内容来重新检测
        markdown_match = re.match(r'^(#{1,4})\s+(.+)$', original_line)
        check_line = markdown_match.group(2) if markdown_match else original_line

        # 时间/日期型文本和数值型正文噪声必须排除，避免误判为标题
        if re.match(TitlePattern.DATE_PATTERN, check_line):
            return None

        # 纯编号型正文内容：例如 “（4） 设备配置”
        if re.match(TitlePattern.PAREN_LIST_PATTERN, check_line):
            return None

        # 数值型统计噪声：例如 "7999 0.5299 0.5799"，不应被当成标题
        if re.match(TitlePattern.NUMERIC_TEXT_PATTERN, check_line):
            return None

        # 极短数字型片段，如 "7999 0.5299999713897705" 也不是标题
        if re.fullmatch(r'[+-]?(?:\d+\.\d+|\d+)(?:\s+[+-]?(?:\d+\.\d+|\d+))+', check_line):
            return None

        # 特殊情况：summary_full只匹配完整行
        if re.match(TitlePattern.SUMMARY_PATTERN, check_line):
            return (TitleLevel.LEVEL_2, check_line)
        
        if re.match(TitlePattern.PRACTICE_PATTERN, check_line):
            return (TitleLevel.LEVEL_2, check_line)
        
        if re.match(TitlePattern.TOC_PATTERN, check_line):
            return (TitleLevel.LEVEL_1, check_line)  # 目录作为一级标题
        
        # 按优先级检测各种模式
        priority_patterns = [
            # 最具体的数字格式
            "decimal_4", "paren_num",
            "decimal_3", "chinese_paren", "letter",
            "decimal_2", "chinese_num_2",
            # 英文和特殊格式
            "chapter_en", "section_en", "lesson", "module",
            "chapter", "section", "unit", "part",
            "appendix", "references",
            # 最后检查单个数字（最通用的模式）
            "single_num",
        ]
        
        for pattern_name in priority_patterns:
            pattern, level = self.patterns[pattern_name]
            match = re.match(pattern, check_line)
            if match:
                # 保留原始文本（或清理后的文本）
                return (level, check_line)
        
        return None
    
    def process_line(self, line: str) -> str:
        """
        处理单行文本，如果是标题则转换为Markdown格式
        """
        # 保留空行和代码块/其他特殊内容
        if not line.strip() or line.strip().startswith("```") or line.strip().startswith("~~~"):
            return line
        
        result = self._detect_title_level(line)
        if result:
            level, title_text = result
            return f"{level.value[1]} {title_text}"
        
        # 如果原行有markdown标记但不是标题，则移除标记
        line_stripped = line.strip()
        markdown_match = re.match(r'^(#{1,4})\s+(.+)$', line_stripped)
        if markdown_match:
            # 移除markdown标记，只保留文本内容
            return markdown_match.group(2)
        
        return line
    
    def process_document(self, markdown_content: str) -> str:
        """
        处理整个Markdown文档
        
        Args:
            markdown_content: Markdown文档内容
            
        Returns:
            处理后的Markdown文档
        """
        lines = markdown_content.split('\n')
        processed_lines = []
        in_code_block = False
        
        for line in lines:
            # 检查代码块的开始和结束
            if line.strip().startswith("```") or line.strip().startswith("~~~"):
                in_code_block = not in_code_block
                processed_lines.append(line)
                continue
            
            # 在代码块内不处理
            if in_code_block:
                processed_lines.append(line)
                continue
            
            # 处理标题行
            processed_line = self.process_line(line)
            processed_lines.append(processed_line)
        
        return '\n'.join(processed_lines)
    
    def process_file(self, input_path: str, output_path: Optional[str] = None) -> str:
        """
        处理文件
        
        Args:
            input_path: 输入文件路径
            output_path: 输出文件路径（可选，如果不指定则覆盖原文件）
            
        Returns:
            处理后的内容
        """
        # 读取文件
        with open(input_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 处理内容
        processed_content = self.process_document(content)
        
        # 写入输出文件
        output_file = output_path or input_path
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(processed_content)
        
        return processed_content
    
    def organize_markdown_content(self, markdown_content: str) -> str:
        """
        专门处理 Markdown 文本内容。
        输入：完整的 md 文本
        输出：修正后的 md 文本
        """
        return self.process_document(markdown_content)

    def organize_markdown_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        overwrite: bool = False,
    ) -> str:
        """
        专门处理 md 文件。

        Args:
            input_path: 输入 md 文件路径
            output_path: 输出路径；如不传则可选择覆盖原文件
            overwrite: 是否覆盖原文件

        Returns:
            处理后的 Markdown 内容
        """
        input_file = Path(input_path)
        if not input_file.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        raw_content = input_file.read_text(encoding='utf-8')
        processed_content = self.organize_markdown_content(raw_content)

        if output_path is not None:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(processed_content, encoding='utf-8')
            return processed_content

        if overwrite:
            input_file.write_text(processed_content, encoding='utf-8')
            return processed_content

        return processed_content


# 使用示例
if __name__ == "__main__":
    # 创建处理器实例
    organizer = MdTitleOrganizer()
    
    # 测试用例
    test_cases = [
        "第1章 人工智能安全概述",
        "Chapter 1 Introduction",
        "Part I Artificial Intelligence",
        "第一节 人工智能安全概述",
        "1.1 人工智能安全的引入",
        "9.1.1 深度伪造概述",
        "1.1.1.1 模型训练过程",
        "Section 1 Overview",
        "一、人工智能安全概述",
        "（一）深度伪造技术",
        "（1）攻击方法介绍",
        "①数据采集方法",
        "A. 数据处理流程",
        "定义：深度伪造",
        "定理1：XXX",
        "本章小结",
        "小结",
        "课后习题",
        "参考文献",
        "附录1",
        "这是普通文本",
        "# 已经是标题",
        "## 第2步",  # 错误的markdown级别，应该改为####
        "## 第1章 简介",  # 错误的markdown级别
        "# 1.2.3 内容",  # 错误的markdown级别
        "#### 1 一级标题",  # 错误的markdown级别
    ]
    
    print("标题处理测试结果：")
    print("-" * 80)
    for test in test_cases:
        result = organizer.process_line(test)
        print(f"输入：{test:40} => 输出：{result}")
    
    # 文件处理示例
    input_md_file = "示例.md"
    output_md_file = "输出示例.md"
    
    # 处理单个文件
    organizer.process_file(input_md_file, output_md_file)
    
    print(f"已处理文件：{input_md_file} => {output_md_file}")
    
    # 处理并覆盖文件
    # organizer.process_file(input_md_file, overwrite=True)
    
    # 处理文件夹中的所有md文件
    # folder_path = "markdown_files"
    # for md_file in Path(folder_path).rglob("*.md"):
    #     organizer.process_file(str(md_file), overwrite=True)
    #     print(f"已处理文件：{md_file}")
    
    # 直接处理一个实际的 md 文件
    input_md = Path("data/md/test.md")
    output_md = Path("data/md/test_processed.md")
    if input_md.exists():
        organizer.organize_markdown_file(str(input_md), str(output_md))
        print(f"已处理: {input_md}")
        print(f"输出到: {output_md}")
        print("--- 预览 ---")
        print(output_md.read_text(encoding='utf-8')[:2000])
    else:
        print(f"未找到测试文件: {input_md}")
