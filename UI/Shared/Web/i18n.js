"use strict";
(() => {
  const bg = {
    "app.title": "Astro Archive", "app.brand": "Astro Archive", "app.tagline": "Твоето небе, подредено.",
    "skip": "Към съдържанието", "nav.label": "Основни функции", "nav.archive": "Архив", "nav.analysis": "Анализ", "nav.preparation": "Подготовка", "nav.settings": "Настройки ⚙",
    "foundation": "Нов интерфейс · първа основа", "legacy.link": "Към работещия интерфейс ↗", "legacy.open": "Отвори сегашния интерфейс ↗",
    "archive.eyebrow": "БИБЛИОТЕКА", "archive.title": "Архив", "archive.subtitle": "Кадри, стакове и готови снимки на едно място.", "archive.refresh": "Обнови",
    "archive.roots": "Моите архиви", "archive.select": "Избор на архив", "archive.fast": "БЪРЗО ЗАРЕЖДАНЕ", "archive.lazy": "Отваря се избраната папка. Снимките се прочитат, когато поискаш преглед.",
    "archive.frames": "Архив на кадри", "archive.stacks": "Архив на стакове", "archive.photos": "Архив на снимки", "archive.savedPath": "Запазено местоположение", "archive.chooseSettings": "Избери от настройките", "archive.noPath": "Не е избрана папка",
    "folder.content": "Съдържание на папката", "folder.current": "Текуща папка", "folder.search": "Търси в тази папка…", "folder.loading": "Зареждане…", "folder.choose": "Избери архив", "folder.direct": "Само съдържанието на избраната папка",
    "folder.emptyTitle": "Място за твоите наблюдения", "folder.loadingPaths": "Зареждане на запазените места за архивите…", "folder.empty": "В тази папка няма файлове или подпапки.", "folder.noMatches": "Няма съвпадения в тази папка.",
    "folder.notConfigured": "Този архив няма избрана папка. Избери я в сегашните настройки.", "folder.noArchive": "Не е избрана архивна папка", "folder.failed": "Папката не може да бъде заредена. Виж съобщението по-горе.", "folder.noContent": "Няма заредено съдържание",
    "folder.counts": "{files} файла · {folders} подпапки · само тази папка", "folder.page": "{first}–{last} от {total}", "folder.loaded": "Заредена папка: {path} · {total} записа.",
    "table.name": "Име", "table.type": "Тип", "table.size": "Размер", "table.modified": "Променен", "table.actions": "Действия", "page.previous": "Предишна страница", "page.next": "Следваща страница",
    "type.folder": "ПАПКА", "type.fits": "FITS", "type.image": "СНИМКА", "type.file": "ФАЙЛ", "type.link": "ВРЪЗКА", "file.open": "Отвори →", "file.preview": "Преглед ↗", "file.openName": "Отвори {name}", "file.previewName": "Преглед на {name}", "file.opened": "Отворен преглед: {name}", "file.linkBlocked": "Тази връзка не се следва автоматично. Отвори я през сегашния интерфейс.",
    "analysis.eyebrow": "КАЧЕСТВО НА НАБЛЮДЕНИЯТА", "analysis.title": "Анализ", "analysis.subtitle": "Измервания, диаграми и сравнение на вечерите.", "analysis.tag": "Python ИЗЧИСЛЕНИЯ", "analysis.measurements": "Познатите измервания",
    "analysis.body": "Съществуващият анализ на кадри и стакове се изпълнява от нашия Python код. Следващата стъпка е прехвърлянето на неговите диаграми и контроли в този интерфейс.", "analysis.open": "Отвори работещия анализ ↗", "analysis.migration": "ПОСТЕПЕННО ПРЕХВЪРЛЯНЕ", "analysis.separation": "Визуализацията се отделя от изчисленията", "analysis.existing": "Този екран още не изпълнява анализ. Бутонът отваря съществуващия изглед, с всички налични инструменти.", "analysis.backend": "Измервания и обработка — Python", "analysis.frontend": "Диаграми и анимации — JavaScript и CSS", "analysis.settings": "Теми, фон и език — в Настройки",
    "preparation.eyebrow": "РАБОТЕН ПОТОК", "preparation.title": "Подготовка за обработване", "preparation.subtitle": "Подбор, опашка и връщане на резултатите.", "preparation.tag": "Python ЗАДАЧИ", "preparation.queue": "Продължи с опашката", "preparation.body": "Подготовката, прехвърлянето на копия и прибирането на резултата остават в съществуващия модул. Новият изглед ще използва същите операции.", "preparation.open": "Отвори работещата подготовка ↗", "preparation.foundation": "ОСНОВА ЗА НОВИЯ ИЗГЛЕД", "preparation.separation": "Една логика, отделно представяне", "preparation.existing": "Тук още няма втора опашка или отделен механизъм за копиране. Бутонът отваря работещия модул и неговите записани задачи.", "preparation.step1": "01 · Избор", "preparation.step2": "02 · Подготовка", "preparation.step3": "03 · Резултат",
    "journal.title": "Дневник", "journal.label": "Дневник на дейностите", "journal.entries": "Последни съобщения", "journal.clear": "Изчисти изгледа", "journal.interface": "ИНТЕРФЕЙС", "route.selected": "Избран изглед: {name}", "workspace.opened": "Отворен е съществуващият Python изглед.",
    "connection.waiting": "Свързване с Python…", "connection.online": "Свързан с Python · локален интерфейс", "connection.offline": "Визуален преглед · без достъп до файлове", "connection.failed": "Няма връзка с Python", "offline.description": "Това е само визуален преглед. Стартирай Start.cmd за връзка с Python и архивите.", "offline.log": "Отворен е само дизайнът. Архивни данни не са заредени.", "offline.action": "Достъпно в Start.cmd", "error.timeout": "Няма отговор от Python в рамките на една минута.", "error.request": "Грешка при заявката.", "error.protocol": "Несъвместима версия на визуалния интерфейс.",
    "settings.title": "Настройки на интерфейса", "settings.close": "Затвори настройките", "settings.appearance": "Външен вид", "settings.language": "Език на новия интерфейс", "settings.languageNote": "Старите Python екрани и оригиналните записи в дневника запазват своя език.", "settings.theme": "Тема", "settings.background": "Фон", "settings.modeTheme": "Фон от темата", "settings.modeColor": "Собствен цвят", "settings.modeImage": "Собствена снимка", "settings.color": "Цвят на фона", "settings.chooseImage": "Избери снимка…", "settings.noImage": "Не е избрана снимка", "settings.imageReady": "Избрана е снимка", "settings.imageNote": "PNG, JPEG или BMP. Копие до 2560 px; леко оцветяване пази четимостта.", "settings.animations": "Плавни анимации", "settings.motionNote": "Зачита се и системната настройка за намалено движение, освен за навигационния индикатор.", "settings.preview": "Промените се виждат веднага. Запазват се с „Запази“.", "settings.save": "Запази", "settings.cancel": "Отказ", "settings.reset": "Върни оригиналните настройки", "settings.saved": "Настройките са запазени.", "settings.previewing": "Преглед на незапазени промени", "settings.saving": "Запазване…", "settings.failed": "Настройките не могат да бъдат заредени.", "settings.hexInvalid": "Въведи цвят като #17304A.",
    "theme.night": "Нощно небе", "theme.violet": "Виолетова мъглявина", "theme.day": "Дневен режим", "theme.custom": "Персонални цветове", "theme.red": "Червен режим за наблюдения",
    "plugins.title": "Плъгини", "plugins.description": "Добавят теми и преводи чрез JSON. Не изпълняват чужд код.", "plugins.install": "Добави плъгин…", "plugins.empty": "Няма инсталирани плъгини.", "plugins.enable": "Включи", "plugins.disable": "Изключи", "plugins.remove": "Премахни", "plugins.enabled": "Включен", "plugins.disabled": "Изключен", "plugins.immediate": "Инсталирането, включването и премахването се запазват веднага. Външният вид се връща към запазените настройки.", "plugins.installed": "Плъгинът е добавен. Включи го, за да ползваш темите или езиците му.", "plugins.changed": "Списъкът с плъгини е обновен.", "plugins.problems": "Проблеми при зареждането", "plugins.busy": "Изчакай…", "plugins.version": "Версия {version}"
  };
  const en = {
    "app.title": "Astro Archive", "app.brand": "Astro Archive", "app.tagline": "Your sky, organised.",
    "skip": "Skip to content", "nav.label": "Main functions", "nav.archive": "Archive", "nav.analysis": "Analysis", "nav.preparation": "Preparation", "nav.settings": "Settings ⚙",
    "foundation": "Modern interface · foundation", "legacy.link": "Open existing interface ↗", "legacy.open": "Open existing interface ↗",
    "archive.eyebrow": "LIBRARY", "archive.title": "Archive", "archive.subtitle": "Frames, stacks and finished images in one place.", "archive.refresh": "Refresh",
    "archive.roots": "My archives", "archive.select": "Choose archive", "archive.fast": "LOAD ON DEMAND", "archive.lazy": "Only the selected folder is listed. Images are read when you open a preview.",
    "archive.frames": "Frame archive", "archive.stacks": "Stack archive", "archive.photos": "Image archive", "archive.savedPath": "Saved location", "archive.chooseSettings": "Choose in settings", "archive.noPath": "No folder selected",
    "folder.content": "Folder contents", "folder.current": "Current folder", "folder.search": "Search this folder…", "folder.loading": "Loading…", "folder.choose": "Choose an archive", "folder.direct": "Contents of this folder only",
    "folder.emptyTitle": "A place for your observations", "folder.loadingPaths": "Loading saved archive locations…", "folder.empty": "This folder has no files or subfolders.", "folder.noMatches": "No matches in this folder.",
    "folder.notConfigured": "No folder is configured for this archive. Choose one in the existing settings.", "folder.noArchive": "No archive folder selected", "folder.failed": "The folder could not be loaded. See the message above.", "folder.noContent": "No contents loaded",
    "folder.counts": "{files} files · {folders} subfolders · this folder only", "folder.page": "{first}–{last} of {total}", "folder.loaded": "Loaded folder: {path} · {total} entries.",
    "table.name": "Name", "table.type": "Type", "table.size": "Size", "table.modified": "Modified", "table.actions": "Actions", "page.previous": "Previous page", "page.next": "Next page",
    "type.folder": "FOLDER", "type.fits": "FITS", "type.image": "IMAGE", "type.file": "FILE", "type.link": "LINK", "file.open": "Open →", "file.preview": "Preview ↗", "file.openName": "Open {name}", "file.previewName": "Preview {name}", "file.opened": "Opened preview: {name}", "file.linkBlocked": "This link is not followed automatically. Open it in the existing interface.",
    "analysis.eyebrow": "OBSERVATION QUALITY", "analysis.title": "Analysis", "analysis.subtitle": "Measurements, plots and comparison of observing nights.", "analysis.tag": "Python MEASUREMENTS", "analysis.measurements": "Your existing measurements", "analysis.body": "The existing frame and stack analysis runs in our Python code. Its plots and controls will be migrated to this interface next.", "analysis.open": "Open existing analysis ↗", "analysis.migration": "GRADUAL MIGRATION", "analysis.separation": "Presentation is separate from computation", "analysis.existing": "This screen does not perform analysis yet. The button opens the existing view with its available tools.", "analysis.backend": "Measurements and processing — Python", "analysis.frontend": "Plots and animations — JavaScript and CSS", "analysis.settings": "Themes, background and language — in Settings",
    "preparation.eyebrow": "WORKFLOW", "preparation.title": "Processing preparation", "preparation.subtitle": "Selection, queue and returning results.", "preparation.tag": "Python TASKS", "preparation.queue": "Continue with your queue", "preparation.body": "Preparation, copying inputs and archiving results remain in the existing module. The new view will use the same operations.", "preparation.open": "Open existing preparation ↗", "preparation.foundation": "FOUNDATION FOR THE NEW VIEW", "preparation.separation": "Shared logic, separate presentation", "preparation.existing": "There is no second queue or separate copy mechanism here. The button opens the existing module and its saved tasks.", "preparation.step1": "01 · Select", "preparation.step2": "02 · Prepare", "preparation.step3": "03 · Result",
    "journal.title": "Activity log", "journal.label": "Activity log", "journal.entries": "Recent messages", "journal.clear": "Clear view", "journal.interface": "INTERFACE", "route.selected": "Selected view: {name}", "workspace.opened": "Opened the existing Python view.",
    "connection.waiting": "Connecting to Python…", "connection.online": "Connected to Python · local interface", "connection.offline": "Visual preview · no file access", "connection.failed": "No connection to Python", "offline.description": "This is a visual preview. Start Start.cmd to connect to Python and your archives.", "offline.log": "Design preview only. No archive data was loaded.", "offline.action": "Available in Start.cmd", "error.timeout": "Python did not respond within one minute.", "error.request": "The request failed.", "error.protocol": "Incompatible interface protocol version.",
    "settings.title": "Interface settings", "settings.close": "Close settings", "settings.appearance": "Appearance", "settings.language": "Language of the new interface", "settings.languageNote": "Existing Python screens and original log messages retain their own language.", "settings.theme": "Theme", "settings.background": "Background", "settings.modeTheme": "Use theme background", "settings.modeColor": "Custom colour", "settings.modeImage": "Custom image", "settings.color": "Background colour", "settings.chooseImage": "Choose image…", "settings.noImage": "No image selected", "settings.imageReady": "An image is selected", "settings.imageNote": "PNG, JPEG or BMP. A copy up to 2560 px; a tint keeps text readable.", "settings.animations": "Smooth animations", "settings.motionNote": "The system reduced-motion preference is respected, except for the navigation indicator.", "settings.preview": "Changes appear immediately. Use Save to keep them.", "settings.save": "Save", "settings.cancel": "Cancel", "settings.reset": "Restore original settings", "settings.saved": "Settings saved.", "settings.previewing": "Previewing unsaved changes", "settings.saving": "Saving…", "settings.failed": "The settings could not be loaded.", "settings.hexInvalid": "Enter a colour such as #17304A.",
    "theme.night": "Night sky", "theme.violet": "Violet nebula", "theme.day": "Day mode", "theme.custom": "Custom colors", "theme.red": "Red observing mode",
    "plugins.title": "Plugins", "plugins.description": "Add themes and translations through JSON. No third-party code is executed.", "plugins.install": "Add plugin…", "plugins.empty": "No plugins installed.", "plugins.enable": "Enable", "plugins.disable": "Disable", "plugins.remove": "Remove", "plugins.enabled": "Enabled", "plugins.disabled": "Disabled", "plugins.immediate": "Installing, enabling and removing plugins is saved immediately. Appearance returns to your saved settings.", "plugins.installed": "Plugin added. Enable it to use its themes or languages.", "plugins.changed": "Plugin list updated.", "plugins.problems": "Loading problems", "plugins.busy": "Please wait…", "plugins.version": "Version {version}"
  };
  Object.assign(bg, {
    "plugins.folder": "Папка на плъгините",
    "plugins.description": "Теми, преводи и функционални модули. Всеки плъгин е в собствена подпапка на Plugins.",
    "plugins.installed": "Плъгинът е добавен. Включи го, за да използваш функциите му."
  });
  Object.assign(en, {
    "plugins.folder": "Open plugins folder",
    "plugins.description": "Themes, translations and workflow modules. Each plugin has its own subfolder in Plugins.",
    "plugins.installed": "Plugin added. Enable it to use its features."
  });
  Object.assign(bg, {
    "settings.title": "Настройки", "settings.sections": "Раздели на настройките", "settings.customization": "Къстамизация", "settings.languageTab": "Език", "settings.done": "Затвори",
    "plugins.scan": "Провери папката", "plugins.scanning": "Търсене на плъгини…", "plugins.list": "Намерени плъгини",
    "plugins.found": "Намерени: {count} · Избери плъгин за неговите настройки.",
    "plugins.empty": "Няма намерени плъгини. Постави модул в Plugins и провери папката отново.",
    "plugins.select": "Избери плъгин от списъка, за да видиш описанието и настройките му.",
    "plugins.activation": "Включи плъгина", "plugins.unavailable": "Недостъпен", "plugins.running": "Работи",
    "plugins.runningNote": "Плъгинът обработва задача. Настройките и изключването ще са достъпни след приключването ѝ."
  });
  Object.assign(en, {
    "settings.title": "Settings", "settings.sections": "Settings sections", "settings.customization": "Customization", "settings.languageTab": "Language", "settings.done": "Close",
    "plugins.scan": "Scan folder", "plugins.scanning": "Looking for plugins…", "plugins.list": "Discovered plugins",
    "plugins.found": "Found: {count} · Select a plugin to view its settings.",
    "plugins.empty": "No plugins found. Place a module in Plugins and scan the folder again.",
    "plugins.select": "Select a plugin to see its description and settings.",
    "plugins.activation": "Enable plugin", "plugins.unavailable": "Unavailable", "plugins.running": "Running",
    "plugins.runningNote": "The plugin is processing a task. Settings and deactivation will be available when it finishes."
  });
  Object.assign(bg, {
    "settings.storage": "Папки и програми…", "archive.import": "Внеси… ▾", "archive.onlyFrames": "Само кадри",
    "diagnostics.title": "Диагностика", "diagnostics.heading": "Бъгове и дневници", "diagnostics.folder": "Подробни лог файлове",
    "diagnostics.restartHint": "Затваря и стартира отново приложението и включените му услуги.", "diagnostics.restartConfirm": "Приложението ще се затвори и ще се опита да се отвори отново. Дистанционната връзка ще се прекъсне. Приключи дистанционните качвания и изтегляния, преди да продължиш. Неприключените действия няма да продължат автоматично. Да продължа ли?",
    "diagnostics.restartBusy": "Има текуща задача. Изчакай да приключи или я спри безопасно, преди да рестартираш.", "diagnostics.restartLocal": "Рестартирането е достъпно от приложението на компютъра.",
    "diagnostics.restarting": "Заявката за рестартиране е приета. Старият прозорец може да се затвори; изчакай приложението да се отвори отново.", "diagnostics.restartFailed": "Рестартирането не е потвърдено.",
    "diagnostics.open": "Отвори Диагностика", "diagnostics.choose": "Избери дневник", "diagnostics.empty": "Няма записи в този дневник за текущата сесия.",
    "diagnostics.legacy": "Стар интерфейс · само за диагностика", "diagnostics.legacyNote": "Помощен изглед за откриване на проблеми. Основните функции работят независимо от него.",
    "analysis.surface": "Кадри, стакове и измервания", "preparation.surface": "Подбор и опашка за обработка",
    "function.loading": "Подготвяне на работния изглед…", "function.ready": "Изгледът е подготвен. Затвори дневника или настройките, за да продължиш.",
    "activity.idle": "Готово за работа", "activity.done": "Завършено", "activity.blocked": "Задачата спря", "activity.cancelled": "Прекъснато",
    "activity.status": "Състояние: {status}", "activity.openDiary": "Състояние и дневници", "activity.noTask": "Няма активна задача. Можеш да прегледаш дневниците.",
    "activity.working": "Работи…", "activity.overall": "общо", "activity.elapsed": "Изминало време: {elapsed}", "activity.step": "Стъпка {step} от {total}",
    "activity.stop": "Спри безопасно", "activity.abandon": "Прекрати задачата", "activity.restart": "Рестартирай приложението",
    "activity.showDiary": "Покажи дневника", "activity.control": "УПРАВЛЕНИЕ НА ЗАДАЧАТА", "activity.hint": "Дневниците са в индикатора за състояние и в Диагностика.",
    "activity.queue": "Активни задачи", "activity.loadingImage": "Зареждане на изображението…",
    "activity.tier.important": "Важна", "activity.tier.normal": "Честа", "activity.tier.ambient": "Фонова"
  });
  Object.assign(en, {
    "settings.storage": "Folders and programs…", "archive.import": "Import… ▾", "archive.onlyFrames": "Frames only",
    "diagnostics.title": "Diagnostics", "diagnostics.heading": "Issues and logs", "diagnostics.folder": "Detailed log files",
    "diagnostics.restartHint": "Closes and starts the application and its enabled services again.", "diagnostics.restartConfirm": "The application will close and try to reopen. The remote connection will be interrupted. Finish remote uploads and downloads before continuing. Unfinished actions will not resume automatically. Continue?",
    "diagnostics.restartBusy": "A task is running. Wait for it to finish or stop it safely before restarting.", "diagnostics.restartLocal": "Restart is available from the application on the computer.",
    "diagnostics.restarting": "The restart request was accepted. The old window may close; wait for the application to reopen.", "diagnostics.restartFailed": "The restart was not confirmed.",
    "diagnostics.open": "Open Diagnostics", "diagnostics.choose": "Choose log", "diagnostics.empty": "No entries in this log for the current session.",
    "diagnostics.legacy": "Legacy interface · diagnostics only", "diagnostics.legacyNote": "An optional troubleshooting view. Main functions work independently of it.",
    "analysis.surface": "Frames, stacks and measurements", "preparation.surface": "Selection and processing queue",
    "function.loading": "Preparing the workspace…", "function.ready": "The workspace is ready. Close the log or settings to continue.",
    "activity.idle": "Ready", "activity.done": "Completed", "activity.blocked": "Task stopped", "activity.cancelled": "Cancelled",
    "activity.status": "Status: {status}", "activity.openDiary": "Activity and logs", "activity.noTask": "No active task. You can browse the logs.",
    "activity.working": "Working…", "activity.overall": "overall", "activity.elapsed": "Elapsed: {elapsed}", "activity.step": "Step {step} of {total}",
    "activity.stop": "Stop safely", "activity.abandon": "Abandon task", "activity.restart": "Restart application",
    "activity.showDiary": "Show log", "activity.control": "TASK CONTROL", "activity.hint": "Logs are available from the activity indicator and Diagnostics.",
    "activity.queue": "Active tasks", "activity.loadingImage": "Loading image…",
    "activity.tier.important": "Important", "activity.tier.normal": "Frequent", "activity.tier.ambient": "Background"
  });
  const staticText = {
    ".skip-link":"skip", ".brand strong":"app.brand", "[data-route='archive']":"nav.archive", "[data-route='analysis']":"nav.analysis", "[data-route='preparation']":"nav.preparation", "#settings-open":"nav.settings",
    "#page-archive .eyebrow":"archive.eyebrow", "#archive-heading":"archive.title", "#page-archive .subtitle":"archive.subtitle", "#refresh span":"archive.refresh", ".sidebar-note .eyebrow":"archive.fast", ".sidebar-note p":"archive.lazy", "#loading":"folder.loading", "#empty h2":"folder.emptyTitle", "#empty [data-workspace]":"legacy.open",
    "#page-archive thead th:nth-child(1)":"table.name", "#page-archive thead th:nth-child(2)":"table.type", "#page-archive thead th:nth-child(3)":"table.size", "#page-archive thead th:nth-child(4)":"table.modified", "#page-archive thead th:nth-child(5) span":"table.actions",
    "#page-analysis .page-heading .eyebrow":"analysis.eyebrow", "#analysis-heading":"analysis.title", "#page-analysis .subtitle":"analysis.subtitle", "#page-analysis .tag":"analysis.tag", "#page-analysis .migration-card:not(.detail-card) h2":"analysis.measurements", "#page-analysis .migration-card:not(.detail-card) p":"analysis.body", "[data-workspace='analysis']":"analysis.open", "#page-analysis .detail-card .eyebrow":"analysis.migration", "#page-analysis .detail-card h2":"analysis.separation", "#page-analysis .detail-card p:not(.eyebrow)":"analysis.existing", "#page-analysis li:nth-child(1)":"analysis.backend", "#page-analysis li:nth-child(2)":"analysis.frontend", "#page-analysis li:nth-child(3)":"analysis.settings",
    "#page-preparation .page-heading .eyebrow":"preparation.eyebrow", "#preparation-heading":"preparation.title", "#page-preparation .subtitle":"preparation.subtitle", "#page-preparation .tag":"preparation.tag", "#page-preparation .migration-card:not(.detail-card) h2":"preparation.queue", "#page-preparation .migration-card:not(.detail-card) p":"preparation.body", "[data-workspace='preparation']":"preparation.open", "#page-preparation .detail-card .eyebrow":"preparation.foundation", "#page-preparation .detail-card h2":"preparation.separation", "#page-preparation .detail-card p:not(.eyebrow)":"preparation.existing", ".workflow-steps span:nth-child(1)":"preparation.step1", ".workflow-steps span:nth-child(2)":"preparation.step2", ".workflow-steps span:nth-child(3)":"preparation.step3", ".journal h2":"journal.title", "#clear-journal":"journal.clear"
  };
  let selected = { id: "bg", locale: "bg", messages: {} };
  function t(key, parameters = {}) {
    const fallback = selected.locale.toLowerCase().startsWith("bg") ? bg : en;
    const text = (Object.hasOwn(selected.messages, key) ? selected.messages[key] : undefined) ?? fallback[key] ?? en[key] ?? bg[key] ?? key;
    return String(text).replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => Object.hasOwn(parameters, name) ? String(parameters[name]) : match);
  }
  function apply() {
    document.documentElement.lang = selected.locale;
    document.title = t("app.title");
    for (const [selector, key] of Object.entries(staticText)) for (const node of document.querySelectorAll(selector)) node.textContent = t(key);
    for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
    for (const node of document.querySelectorAll("[data-i18n-aria-label]")) node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
    const labels = [[".navigation", "nav.label"], [".archive-sidebar", "archive.select"], [".content-panel", "folder.content"], ["#breadcrumbs", "folder.current"], ["#search", "folder.search"], ["#previous", "page.previous"], ["#next", "page.next"], [".journal", "journal.label"], ["#journal-entries", "journal.entries"]];
    for (const [selector, key] of labels) document.querySelector(selector)?.setAttribute("aria-label", t(key));
    document.getElementById("search").placeholder = t("folder.search");
  }
  window.AstroArchiveI18n = {
    t, apply, get locale() { return selected.locale; },
    select(language) {
      // Invalid or unfamiliar locale identifiers still get a readable fallback.
      let locale = language?.locale || "bg";
      try { new Intl.NumberFormat(locale); } catch { locale = "en"; }
      selected = { id: language?.id || "bg", locale, messages: language?.messages || {} };
      apply();
    }
  };
  apply();
})();

