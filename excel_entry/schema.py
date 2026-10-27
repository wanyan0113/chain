"""One authoritative workbook contract; labels do not depend on entity names."""
TABLES = {
    'table7': ('对象清单', 'id', [
        ('side', '所属方', 'text'), ('name', '名称', 'text'),
        ('id', '对象编号', 'text'), ('count', '数量', 'int')]),
    'table8': ('探测能力', 'platform', [
        ('platform', '平台编号', 'text'), ('longitude_column_raw', '原经度列', 'text'),
        ('latitude_column_raw', '原纬度列', 'text'), ('altitude_ft', '平台高度（ft）', 'number'),
        ('insight_artifact', '资源编号', 'text'), ('target_types', '目标类型（用分号分隔）', 'list'),
        ('detection_range_nm', '探测距离（n mile）', 'number'), ('precision_raw', '精度原值（单位待确认）', 'number')]),
    'table9': ('作用能力', 'manifestation_artifact', [
        ('platform', '平台编号', 'text'), ('manifestation_artifact', '资源编号', 'text'),
        ('target_types', '目标类型（用分号分隔）', 'list'), ('max_range_nm', '最大作用距离（n mile）', 'number'),
        ('min_range_nm', '最小作用距离（n mile）', 'number'), ('resonance_value', '作用值（单位待确认）', 'number'),
        ('max_target_speed_kn', '最大目标速度（kn）', 'number'),
        ('min_target_altitude_ft', '最小目标高度（ft）', 'number'), ('max_target_altitude_ft', '最大目标高度（ft）', 'number'),
        ('min_activation_altitude_ft', '最小释放高度（ft）', 'number'), ('max_activation_altitude_ft', '最大释放高度（ft）', 'number'),
        ('attunement_probability_percent', '概率（百分数0至100）', 'number')]),
    'table11': ('任务', 'mission', [
        ('mission', '任务名称', 'text'), ('target_type', '目标类型', 'text'),
        ('longitude_column_raw', '原经度列', 'text'), ('latitude_column_raw', '原纬度列', 'text'),
        ('target_altitude_ft', '目标高度（ft）', 'number'), ('target_speed_kn', '目标速度（kn）', 'number'),
        ('target_impedance_threshold', '目标阈值（单位待确认）', 'number')]),
}
META_HEADERS = ['类别', '名称', '内容']
META_LABELS = {
    '基本信息': {'checked_on': '核对日期（YYYY-MM-DD文本）', 'scope': '案例说明', 'source_alignment_policy': '来源整理说明'},
    '来源': {'table7': '对象清单资料', 'table8_table9': '能力表资料', 'table11': '任务资料', 'case_text': '正文资料',
             'table10': '规则表资料', 'resource_count_text': '数量说明资料'},
    '单位': {'coordinates': '坐标', 'altitude': '高度', 'distance': '距离', 'speed': '速度',
             'attunement_probability': '概率', 'insight_precision': '精度', 'resonance_value': '作用值', 'impedance_threshold': '目标阈值'},
}
FACT_HEADERS = ['主体', '关系', '客体', '来源定位']
ISSUE_HEADERS = ['疑点代码', '关联对象说明', '疑点说明', '登记状态原文', '来源对应说明', '来源文件（每行一个）', '关联字段（每行一个）']
INSTRUCTIONS = [
    ['项目', '填写方法'],
    ['开始', '先用案例示例.xlsx试导入；新案例使用空白模板.xlsx。'],
    ['数据', '前四张表一行一条记录。保留表名和第一行列名，可增加、删除或排序数据行。'],
    ['数值', '填数字，不要加单位；概率填90表示90%。0是有效数值，空白会报缺失。'],
    ['目标类型', '多个类型用英文分号分隔；坐标按原资料文本填写。'],
    ['来源', '案例说明中填写来源文件名称或路径；当前入口接收整理好的表格，不读取图片内容。'],
    ['补充资料', '案例说明登记日期、单位、名称对应与缺失事实；事实表可登记正文关系。'],
    ['疑点', '疑点表是人工登记。关联字段格式：工作表名::唯一编号::列名，每行一个；整表只填表名。'],
    ['修改数据', '修改原值后需人工复核已有疑点文字。程序不自动改写来源说明或判断所有业务规则。'],
    ['结果', '保存并关闭Excel，在本地网页选择文件后点导入；结果来自本次实际运行。'],
]
