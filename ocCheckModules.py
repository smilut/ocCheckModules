import argparse
import os
import subprocess
import git
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

global logger


class OCcommand:
    """Структура описания параметров команды 1С"""
    command_line: str
    time_out: int
    desc: str
    successful_msg: str


def init_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", help="set path to config file", type=str, default="")
    args = parser.parse_args()

    return args


def get_conf_path() -> str:
    args = init_args()
    conf_path = args.conf
    if conf_path == "":
        conf_path = os.path.join(os.getcwd(), "config.json")

    return conf_path


def init_configuration() -> dict:
    conf_path = get_conf_path()

    with open(conf_path, mode="r", encoding="utf-8") as conf_file:
        conf = json.load(conf_file)

    return conf


def start_logger(conf: dict):
    log_cfg = conf['logging']
    log_path: str = log_cfg['path']

    global logger

    rotate_time = log_cfg['rotate_time']
    rotate_interval = log_cfg['rotate_interval']
    if rotate_time == 'midnight':
        handler = TimedRotatingFileHandler(log_path, when=rotate_time, backupCount=log_cfg['copy_count'],
                                           encoding='utf-8')
    else:
        handler = TimedRotatingFileHandler(log_path, when=rotate_time, interval=rotate_interval,
                                           backupCount=log_cfg['copy_count'],
                                           encoding='utf-8')

    handler.setFormatter(logging.Formatter('%(asctime)s; %(levelname)s; %(name)s; %(message)s; %(desc)s',
                                           defaults={"desc": ''}))

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.getLevelName(log_cfg['level']))
    logger.addHandler(handler)


def read_oc_log_file(log_path):
    log_data = ''
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding="utf_8_sig") as oc_log:
                log_data = oc_log.read().rstrip()
            try:
                os.remove(log_path)
            except Exception:
                logger.exception("Ошибка удаления лога 1С")
    except Exception:
        log_data = "Ошибка чтения лога 1С."
        logger.exception(log_data)

    return log_data


# Читает лог выполнения операции при запуске 1С
# в пакетном режиме
# Проблема в том, что данный файл формируется не всегда.
def read_oc_log(conf: dict) -> str:
    log_path = conf['onec']['log_file_path']
    oc_msg = read_oc_log_file(log_path)
    return oc_msg


def execute_command(conf: dict, oc_command: OCcommand):
    logger.info(f'Начало: {oc_command.desc}')
    logger.info("Команда: %s", oc_command.command_line)
    subprocess.run(oc_command.command_line, shell=False, timeout=oc_command.time_out)
    logger.info(f'Завершено: {oc_command.desc}')


# приводит базу даных в исходное состояние перед
# запуском скрипта. Т.к. если основная конфигурация
# не соответсвует конфигурации базы данных запрос
# на продолжение блокирует генерацию истории хранилища
# из отчета по хранилищу
def restore_bd_configuration(conf):
    global logger

    command_line = get_onec_command_line(conf, 'DESIGNER')
    # restore_params = ' /RollbackCfg'
    restore_params = ' /RestoreIB "{}"'.format(conf['info_base']['empty_db_path'])

    oc_command = OCcommand()
    oc_command.command_line = command_line + restore_params
    oc_command.desc = 'Восстановление конфигурации'
    oc_command.time_out = conf['onec']['timeout']
    oc_command.successful_msg = 'Возврат к конфигурации БД успешно завершен'

    execute_command(conf, oc_command)
    oc_msg = read_oc_log(conf)
    logger.info(f'Сообщение 1С: {oc_msg}')
    if oc_msg != oc_command.successful_msg and oc_msg != '':
        err_msg = f'Ошибка выполнения {oc_command.command_line} \n сообщение {oc_msg}'
        raise ValueError(err_msg)


def get_storage_data_path(conf) -> str:
    return conf['storage']['version_path']


# формирует общую часть командной строки запуска 1С
# отвечает за подключение к информационной базе
def get_onec_command_line(conf, start_type: str) -> str:
    onec = conf['onec']
    info_base = conf['info_base']
    if info_base['windows_auth']:
        wa = ''
        user = ''
        password = ''
    else:
        wa = '/WA-'

        user = ''
        if info_base['user'] == '':
            raise ValueError('Не указано имя пользователя')
        else:
            user = '/N{}'.format(info_base['user'])

        password = ''
        if info_base['password'] != '':
            password = '/P{}'.format(info_base['password'])

    onec_command_line = '{start_path} {start_type} {wa_flag} /DisableStartupDialogs {user_name} ' \
                        '{passwd} /L ru /VL ru /IBConnectionString "{connection_string}" ' \
                        '/DumpResult "{result_path}"' \
                        ' '.format(start_path=onec['start_path'],
                                   start_type=start_type,
                                   wa_flag=wa,
                                   user_name=user,
                                   passwd=password,
                                   # 1C требует двойных кавычек внутри строки
                                   connection_string=info_base['connection_string'].replace('"', '""'),
                                   result_path=onec['result_dump_path'])
    return onec_command_line


def update_to_storage_version_command(conf: dict) -> OCcommand:
    onec = conf['onec']
    storage = conf['storage']

    command_line = get_onec_command_line(conf, 'DESIGNER')

    if storage['password'] == "":
        passwd_flag = ""
    else:
        passwd_flag = storage['password']

    update_param_str = '/ConfigurationRepositoryF "{storage_path}" ' \
                       '/ConfigurationRepositoryN {storage_user} {storage_passwd_flag} ' \
                       '/ConfigurationRepositoryUpdateCfg -force ' \
                       '/Out "{log_path}" '.format(storage_path=storage['path'],
                                                   storage_user=storage['user'],
                                                   storage_passwd_flag=passwd_flag,
                                                   log_path=onec['log_file_path'])

    oc_command = OCcommand()
    oc_command.command_line = command_line + ' ' + update_param_str
    oc_command.desc = 'Обновление из хранилища'
    oc_command.time_out = onec['update_timeout']
    oc_command.successful_msg = 'Обновление конфигурации из хранилища успешно завершено'

    return oc_command


# обновляет основную конфигурацию до указанной версии
# из хранилища
def update_to_storage_version(conf: dict):
    oc_command = update_to_storage_version_command(conf)
    execute_command(conf, oc_command)

    oc_msg = read_oc_log(conf)
    logger.info(f'Сообщение 1С: {oc_msg}')
    if oc_msg != oc_command.successful_msg:
        err_msg = f'Ошибка выполнения {oc_command.command_line} \n сообщение {oc_msg}'
        raise ValueError(err_msg)


"""/CheckConfig 
https://its.1c.ru/db/v8320doc#bookmark:adm:TI000000529
Выполнить централизованную проверку конфигурации. Допустимо использование следующих параметров:

-ConfigLogIntegrity ‑ проверка логической целостности конфигурации. 
    Стандартная проверка, обычно выполняемая перед обновлением базы данных;
-IncorrectReferences ‑ поиск некорректных ссылок. Поиск ссылок на удаленные объекты. 
    Выполняется по всей конфигурации, включая права, формы, макеты, интерфейсы и т. д. 
    Также осуществляется поиск логически неправильных ссылок;
-ThinClient ‑ синтаксический контроль модулей для режима эмуляции среды управляемого приложения (тонкий клиент),
    выполняемого в файловом режиме;
-WebClient ‑ синтаксический контроль модулей в режиме эмуляции среды веб-клиента;
-MobileClient ‑ синтаксический контроль модулей в режиме эмуляции среды мобильного клиента;
-MobileClientStandalone ‑ синтаксический контроль модулей в режиме эмуляции среды мобильного клиента, 
    работающего в автономном режиме;
-Server ‑ синтаксический контроль модулей в режиме эмуляции среды сервера «1С:Предприятия»;
-ExternalConnection ‑ синтаксический контроль модулей в режиме эмуляции среды внешнего соединения, 
    выполняемого в файловом режиме;
-ExternalConnectionServer ‑ синтаксический контроль модулей в режиме эмуляции среды внешнего соединения, 
    выполняемого в клиент-серверном режиме;
-MobileAppClient ‑ синтаксический контроль модулей в режиме эмуляции среды мобильной платформы, 
    выполняемой в клиентском режиме запуска;
-MobileAppServer ‑ синтаксический контроль модулей в режиме эмуляции среды мобильной платформы, 
    выполняемой в серверном режиме запуска;
-ThickClientManagedApplication ‑ синтаксический контроль модулей в режиме эмуляции среды 
    управляемого приложения (толстый клиент), выполняемого в файловом режиме;
-ThickClientServerManagedApplication ‑ синтаксический контроль модулей в режиме эмуляции среды 
    управляемого приложения (толстый клиент), выполняемого в клиент-серверном режиме;
-ThickClientOrdinaryApplication ‑ синтаксический контроль модулей в режиме эмуляции среды 
    обычного приложения (толстый клиент), выполняемого в файловом режиме;
-ThickClientServerOrdinaryApplication ‑ синтаксический контроль модулей в режиме эмуляции среды 
    обычного приложения (толстый клиент), выполняемого в клиент-серверном режиме;
-DistributiveModules ‑ поставка модулей без исходных текстов. 
    В случае если в настройках поставки конфигурации для некоторых модулей указана 
    поставка без исходных текстов, проверяется возможность генерации образов этих модулей;
-UnreferenceProcedures ‑ поиск неиспользуемых процедур и функций. 
    Поиск локальных (не экспортных) процедур и функций, на которые отсутствуют ссылки. 
    В том числе осуществляется поиск неиспользуемых обработчиков событий;
-HandlersExistence ‑ проверка существования назначенных обработчиков. 
    Проверка существования обработчиков событий интерфейсов, форм и элементов управления;
-EmptyHandlers ‑ поиск пустых обработчиков. Поиск назначенных обработчиков событий, 
    в которых не выполняется никаких действий. 
    Существование таких обработчиков может привести к снижению производительности системы;
-ExtendedModulesCheck ‑ проверка обращений к методам и свойствам объектов «через точку» 
    (для ограниченного набора типов); 
    проверка правильности строковых литералов ‑ параметров некоторых функций, таких как ПолучитьФорму();
-CheckUseModality ‑ режим поиска использования в модулях методов, связанных с модальностью. 
    Параметр используется только вместе с параметром -ExtendedModulesCheck.
-CheckUseSynchronousCalls ‑ режим поиска использования в модулях синхронных методов. 
    Параметр используется только вместе с параметром -ExtendedModulesCheck.
-UnsupportedFunctional ‑ выполняется поиск функциональности, которая не может быть выполнена 
    в приложении для мобильного устройства. Проверка в этом режиме показывает:
        ● наличие в конфигурации метаданных, классы которых не реализованы на мобильной платформе;
        ● наличие в конфигурации планов обмена, у которых установлено свойство Распределенная информационная база;
        ● использование типов, которые не реализованы на мобильной платформе:
        ● в свойствах Тип реквизитов метаданных, констант, параметров сеанса;
        ● в свойстве Тип параметра команды объекта конфигурации Команда;
        ● в свойстве Тип реквизитов и колонок реквизита формы;
        ● наличие форм с типом формы Обычная;
        ● наличие в форме элементов управления, которые не реализованы на мобильной платформе. 
            Проверка не выполняется для форм, у которых свойство Назначения использования 
            не предполагает использование на мобильном устройстве;
        ● сложный состав рабочего стола (использование более чем одной формы).
-MobileClientDigiSign ‑ выполняет проверку цифровой подписи конфигурации для мобильного клиента;
-Extension ‑ выполнить заданные проверки для указанного расширения.
-AllExtensions ‑ выполнить заданные проверки для всех расширений.
"""
def check_configuration_command(conf: dict):
    git_options = conf['git']
    check_options = conf['check']
    check_path = os.path.join(git_options['check_res_path'], check_options['result_file_name'])
    command_line = get_onec_command_line(conf, 'DESIGNER')

    check_param_str = '/CheckConfig {check_flags} /Out "{check_file}"'.format(check_flags=check_options['flags'],
                                                                            check_file=check_path)
    oc_command = OCcommand()
    oc_command.command_line = command_line + ' ' + check_param_str

    oc_command.desc = 'Проверка конфигурации'
    oc_command.time_out = check_options['timeout']
    oc_command.successful_msg = ''

    return oc_command


# выгружает основную конфигурацию в git и выполняет commit
# от имени пользователя поместившего версию в хранилище
def check_configuration(conf: dict):
    oc_command = check_configuration_command(conf)
    execute_command(conf, oc_command)


def git_commit_check_storage_version(conf: dict):
    global logger

    logger.info('Начало git add')
    git_options = conf['git']
    repo = git.Repo(git_options['path'], search_parent_directories=False)
    repo.index.add('*')
    logger.info('Завершен git add;')

    logger.info('Начало git commit')
    git_author = '{author} <{mail}>'.format(author=os.getlogin(), mail=git_options['email'])
    label = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    repo.git.commit('-m', label, author=git_author)
    logger.info('Завершен git commit;')


def git_push(conf: dict):
    global logger

    logger.info('Начало git push')
    git_options = conf['git']
    repo = git.Repo(git_options['path'], search_parent_directories=False)
    try:
        origin = repo.remotes['origin']
    except IndexError as ie:
        logger.exception("Ошибка получения удаленного репозитария")
        raise ie

    # for linux only
    # origin.push(kill_after_timeout=git_options['push_timeout'])
    origin.push()
    logger.info('Выполнение git push завершено')

    pass


def check_last_storage_ver():
    conf = init_configuration()
    start_logger(conf)
    try:
        logger.info('Запуск скрипта')
        restore_bd_configuration(conf)
        update_to_storage_version(conf)
        check_configuration(conf)
        git_commit_check_storage_version(conf)
        git_push(conf)
        logger.debug('Завершение скрипта')
    except Exception as e:
        logger.exception('Script error')
        raise e


if __name__ == "__main__":
    check_last_storage_ver()
    pass
