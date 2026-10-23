"""Distinct codes for road licences and tractor / self-propelled machine licences."""
from django.core.exceptions import ValidationError

ROAD = [
    ('M','Мопеды'),('A','Мотоциклы'),('A1','Лёгкие мотоциклы'),
    ('B','Легковые автомобили'),('B1','Трициклы и квадрициклы'),('BE','Автомобили B с прицепом'),
    ('C','Грузовые автомобили'),('C1','Грузовые автомобили до 7,5 т'),('CE','Грузовые автомобили с прицепом'),('C1E','Автомобили C1 с прицепом'),
    ('D','Автобусы'),('D1','Малые автобусы'),('DE','Автобусы с прицепом'),('D1E','Автобусы D1 с прицепом'),
    ('Tm','Трамваи'),('Tb','Троллейбусы'),
]
TRACTOR = [('A_I','Внедорожные мототранспортные средства'),('A_II','Внедорожные автомобили до 3,5 т'),
           ('A_III','Внедорожные автомобили свыше 3,5 т'),('A_IV','Внедорожные пассажирские автомобили'),
           ('B','Колёсные и гусеничные машины до 25,7 кВт'),('C','Колёсные машины от 25,7 до 110,3 кВт'),
           ('D','Колёсные машины свыше 110,3 кВт'),('E','Гусеничные машины свыше 25,7 кВт'),('F','Самоходные сельскохозяйственные машины')]

def tractor_label(code):
    return code.replace('_','') if not code.startswith('A_') else code.replace('_',' ')

DRIVER_CATEGORY_CHOICES = [
    ('Водительское удостоверение', [('road_'+code,code+' — '+description) for code,description in ROAD]),
    ('Удостоверение тракториста-машиниста', [('tractor_'+code,tractor_label(code)+' — '+description) for code,description in TRACTOR]),
]
DRIVER_CATEGORY_SHORT = {**{'road_'+code:'ВУ · '+code for code,_ in ROAD},
                         **{'tractor_'+code:'Трактор · '+tractor_label(code) for code,_ in TRACTOR}}

def validate_driver_categories(value):
    if not isinstance(value,list) or any(not isinstance(code,str) or code not in DRIVER_CATEGORY_SHORT for code in value):
        raise ValidationError('Выберите категории водителя из списка.')
    if len(value)!=len(set(value)):
        raise ValidationError('Категория водителя не должна повторяться.')

DRIVER_CLASS_CHOICES = [('1','1-й класс'),('2','2-й класс'),('3','3-й класс')]

def validate_driver_classes(value):
    if not isinstance(value,list) or any(not isinstance(code,str) or code not in {'1','2','3'} for code in value):
        raise ValidationError('Выберите квалификационный класс водителя из списка.')
    if len(value)!=len(set(value)):
        raise ValidationError('Класс водителя не должен повторяться.')
