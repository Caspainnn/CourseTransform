#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from MdTitleOrganizer import MdTitleOrganizer
from test_data import TEST_DATA, CATEGORY_COUNTS

class TitleOrganizerTester:
    """标题处理器测试框架"""
    
    def __init__(self):
        self.organizer = MdTitleOrganizer()
        self.results = []
        self.pass_count = 0
        self.fail_count = 0
    
    def run_tests(self):
        """运行所有测试"""
        print("\n" + "=" * 100)
        print("MD标题处理器自动化测试")
        print("=" * 100 + "\n")
        
        for i, (input_text, expected_output, category, description) in enumerate(TEST_DATA, 1):
            actual_output = self.organizer.process_line(input_text)
            is_pass = actual_output == expected_output
            
            result = {
                'index': i,
                'input': input_text,
                'expected': expected_output,
                'actual': actual_output,
                'category': category,
                'description': description,
                'pass': is_pass
            }
            self.results.append(result)
            
            if is_pass:
                self.pass_count += 1
                status = "✓ PASS"
            else:
                self.fail_count += 1
                status = "✗ FAIL"
            
            # 格式化输出
            print(f"[{i:3d}] {status} | {category:15} | {description:20}")
            print(f"      输入:  {self._truncate(input_text, 60):60}")
            
            if not is_pass:
                print(f"      预期:  {self._truncate(expected_output, 60):60}")
                print(f"      实际:  {self._truncate(actual_output, 60):60}")
            
            print()
    
    def print_summary(self):
        """打印测试总结"""
        total = len(self.results)
        success_rate = (self.pass_count / total * 100) if total > 0 else 0
        
        print("=" * 100)
        print("测试总结")
        print("=" * 100)
        print(f"总测试用例数:  {total}")
        print(f"通过:          {self.pass_count}")
        print(f"失败:          {self.fail_count}")
        print(f"成功率:        {success_rate:.1f}%")
        print()
        
        # 按类别统计
        print("按类别统计:")
        print("-" * 50)
        category_results = {}
        for result in self.results:
            cat = result['category']
            if cat not in category_results:
                category_results[cat] = {'pass': 0, 'fail': 0}
            if result['pass']:
                category_results[cat]['pass'] += 1
            else:
                category_results[cat]['fail'] += 1
        
        for category in sorted(category_results.keys()):
            stats = category_results[category]
            total_cat = stats['pass'] + stats['fail']
            rate = (stats['pass'] / total_cat * 100) if total_cat > 0 else 0
            status = "✓" if stats['fail'] == 0 else "✗"
            print(f"{status} {category:20} : {stats['pass']:2}/{total_cat:2} ({rate:5.1f}%)")
        
        print("=" * 100)
    
    def print_failed_cases(self):
        """打印失败的测试用例"""
        failed = [r for r in self.results if not r['pass']]
        if not failed:
            return
        
        print("\n" + "=" * 100)
        print("失败的测试用例详情")
        print("=" * 100 + "\n")
        
        for i, result in enumerate(failed, 1):
            print(f"[失败 {i}] {result['category']} - {result['description']}")
            print(f"  输入:  {result['input']!r}")
            print(f"  预期:  {result['expected']!r}")
            print(f"  实际:  {result['actual']!r}")
            print()
    
    @staticmethod
    def _truncate(text, length):
        """截断文本"""
        if len(text) > length:
            return text[:length-3] + "..."
        return text
    
    def export_report(self, filename="test_report.txt"):
        """导出测试报告"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("MD标题处理器测试报告\n")
            f.write("=" * 100 + "\n\n")
            
            for result in self.results:
                status = "✓ PASS" if result['pass'] else "✗ FAIL"
                f.write(f"[{result['index']:3d}] {status}\n")
                f.write(f"类别:   {result['category']}\n")
                f.write(f"说明:   {result['description']}\n")
                f.write(f"输入:   {result['input']!r}\n")
                f.write(f"预期:   {result['expected']!r}\n")
                f.write(f"实际:   {result['actual']!r}\n")
                f.write("\n")
            
            f.write("\n" + "=" * 100 + "\n")
            f.write("测试总结\n")
            f.write("=" * 100 + "\n")
            f.write(f"总数:   {len(self.results)}\n")
            f.write(f"通过:   {self.pass_count}\n")
            f.write(f"失败:   {self.fail_count}\n")
            success_rate = (self.pass_count / len(self.results) * 100) if self.results else 0
            f.write(f"成功率: {success_rate:.1f}%\n")
        
        print(f"\n测试报告已导出到: {filename}")


if __name__ == "__main__":
    # 首先打印测试数据集统计
    print("\n测试数据集统计:")
    print("=" * 50)
    for category, count in CATEGORY_COUNTS.items():
        print(f"{category:20} : {count:3} 个测试用例")
    print("=" * 50)
    print(f"{'总计':20} : {len(TEST_DATA):3} 个测试用例")
    
    # 运行测试
    tester = TitleOrganizerTester()
    tester.run_tests()
    tester.print_summary()
    tester.print_failed_cases()
    tester.export_report()
