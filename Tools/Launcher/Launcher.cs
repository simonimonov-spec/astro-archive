// Windows .NET Framework launcher; no machine-wide Python or administrator rights.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class Launcher : Form
{
    const string Releases = "https://github.com/simonimonov-spec/astro-archive/releases";
    const string ReleaseAPI = "https://api.github.com/repos/simonimonov-spec/astro-archive/releases?per_page=100";
    const string PythonURL = "https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip";
    const string PipURL = "https://pypi.org/pypi/pip/25.3/json";
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8000000 };
    readonly string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 25 };
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly List<Button> buttons = new List<Button>();
    ListBox issues;
    readonly CancellationTokenSource stop = new CancellationTokenSource();
    readonly bool preview;
    string status = "Проверка на файловете…", detail = "Подготвяме твоята вселена", phase = "ПОДГОТОВКА";
    float progress = .04f;
    Icon logo;
    bool busy;
    Dictionary<string, object> manifest;
    string Runtime { get { return Path.Combine(root, ".runtime", "python"); } }
    string Python { get { return Path.Combine(Runtime, "python.exe"); } }
    string State { get { return Path.Combine(root, "Backend", "Cache", "data", "launcher.json"); } }

    [STAThread]
    static int Main(string[] args)
    {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Contains("--native-dialog-probe")) { WriteReply(new { ready = true, text = "Избор на файлове" }); return 0; }
        if (args.Contains("--native-dialog")) return NativeDialog();
        using (var window = new Launcher(args.Contains("--preview") || args.Contains("--render"), args.Contains("--render")))
        {
            if (args.Contains("--render"))
            {
                int n = Array.IndexOf(args, "--render");
                if (n + 1 == args.Length) return 2;
                window.CreateControl(); window.progress = .64f;
                window.phase = "ПРОВЕРКА НА КОМПОНЕНТИТЕ";
                window.status = "Всичко е на правилната орбита";
                window.detail = "Python • библиотеки • файлове • версия";
                if (args.Contains("--missing"))
                {
                    window.phase = "НУЖНИ СА ДОПЪЛНИТЕЛНИ КОМПОНЕНТИ";
                    window.status = "Нужно е изтегляне преди стартиране";
                    window.detail = "Изтеглянето започва само след избор на „Изтегли“";
                    window.ShowIssues(new[] { "Самостоятелна Python среда 3.13.9 x64", "NumPy • SciPy • Astropy • Photutils • Pillow", "Точните версии се проверяват преди отваряне." });
                    window.AddButton("Виж какво липсва", delegate { }); window.AddButton("Изтегли", delegate { }); window.AddButton("Отказ", delegate { });
                }
                if (args.Contains("--update"))
                {
                    window.phase = "ИМА НОВА ВЕРСИЯ"; window.status = "Налична е нова версия на Astro Archive";
                    window.detail = "Текущото копие ще остане запазено";
                    window.AddButton("Изтегли", delegate { }); window.AddButton("Пропусни сега", delegate { }); window.AddButton("Пропусни версията", delegate { });
                }
                window.ShowInTaskbar = false; window.Opacity = 0; window.Show(); window.Refresh();
                using (var bitmap = new Bitmap(window.Width, window.Height))
                { window.DrawToBitmap(bitmap, new Rectangle(Point.Empty, window.Size)); bitmap.Save(args[n + 1]); }
                return 0;
            }
            if (args.Contains("--self-test")) return window.SelfTest();
            string identity = Hash(Encoding.UTF8.GetBytes(window.root.ToLowerInvariant()));
            using (var mutex = new Mutex(false, "Local\\AstroArchive-Launcher-" + identity))
            {
                bool owned;
                try { owned = mutex.WaitOne(0); } catch (AbandonedMutexException) { owned = true; }
                if (!owned) return 0;
                try { Application.Run(window); } finally { mutex.ReleaseMutex(); }
            }
        }
        return 0;
    }

    static void WriteReply(object value)
    {
        using (var stream = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)))
        { stream.Write(Json.Serialize(value)); stream.Flush(); }
    }
    static int NativeDialog()
    {
        try
        {
            string input;
            using (var stream = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8)) input = stream.ReadToEnd();
            var request = Dict(Json.DeserializeObject(input));
            var options = Dict(request["options"]); string kind = Str(request, "kind");
            object value = "";
            using (var owner = new Form { TopMost = true, Opacity = 0, ShowInTaskbar = false, Width = 1, Height = 1 })
            {
                owner.Show();
                if (kind == "folder")
                {
                    using (var dialog = new FolderBrowserDialog { Description = Str(options, "title"), SelectedPath = Str(options, "initialdir") })
                        if (dialog.ShowDialog(owner) == DialogResult.OK) value = dialog.SelectedPath;
                }
                else
                {
                    if (kind != "files" && kind != "file" && kind != "save") throw new ArgumentException("Непознат файлов диалог.");
                    using (FileDialog dialog = kind == "save" ? (FileDialog)new SaveFileDialog() : new OpenFileDialog())
                    {
                        dialog.Title = Str(options, "title"); dialog.InitialDirectory = Str(options, "initialdir");
                        dialog.FileName = Str(options, "initialfile"); dialog.DefaultExt = Str(options, "defaultextension").TrimStart('.');
                        dialog.RestoreDirectory = true;
                        if (options.ContainsKey("filetypes"))
                        {
                            var filters = new List<string>();
                            foreach (var pair in ArrayValue(options["filetypes"]))
                            {
                                var parts = ArrayValue(pair);
                                string pattern = Convert.ToString(parts[1]).Replace(' ', ';');
                                filters.Add(Convert.ToString(parts[0]).Replace('|', ' ') + " (" + pattern + ")|" + pattern);
                            }
                            if (filters.Count > 0) dialog.Filter = string.Join("|", filters);
                        }
                        if (dialog is OpenFileDialog) ((OpenFileDialog)dialog).Multiselect = kind == "files";
                        if (kind == "files") value = new string[0];
                        if (dialog.ShowDialog(owner) == DialogResult.OK)
                            value = kind == "files" ? (object)((OpenFileDialog)dialog).FileNames : dialog.FileName;
                    }
                }
            }
            WriteReply(new { value = value }); return 0;
        }
        catch (Exception error) { WriteReply(new { error = error.Message }); return 1; }
    }

    Launcher(bool isPreview, bool rendering)
    {
        preview = isPreview; ClientSize = new Size(600, 590); Text = "Astro Archive — стартиране";
        FormBorderStyle = FormBorderStyle.FixedSingle; MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen; BackColor = Color.FromArgb(10, 16, 29);
        DoubleBuffered = true; Font = new Font("Segoe UI", 10);
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("logo.ico"))
            if (stream != null) { logo = new Icon(stream); Icon = logo; }
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("manifest.json"))
        using (var reader = new StreamReader(stream)) manifest = Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
        timer.Tick += delegate { Invalidate(new Rectangle(0, 0, Width, 460)); }; timer.Start();
        Shown += async delegate
        {
            if (rendering) return;
            if (preview)
            {
                phase = "ПРЕГЛЕД НА НОВОТО СТАРТИРАНЕ";
                status = "Проверка на нужните файлове…";
                detail = "Демонстрация • без изтегляне и промяна на данни";
                AddButton("Затвори прегледа", Close);
                timer.Tick += delegate { progress = .15f + .7f * (float)((Math.Sin(clock.Elapsed.TotalSeconds / 3) + 1) / 2); };
            }
            else await Guard(Start);
        };
        FormClosing += delegate { stop.Cancel(); };
    }

    static string Hash(byte[] bytes)
    { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    static Dictionary<string, object> Dict(object value) { return (Dictionary<string, object>)value; }
    static object[] ArrayValue(object value) { return ((IEnumerable)value).Cast<object>().ToArray(); }
    static string Str(Dictionary<string, object> d, string key) { return d.ContainsKey(key) ? Convert.ToString(d[key]) : ""; }
    void Update(string text, string sub, float value)
    { status = text; detail = sub; progress = value; Invalidate(); }
    void ClearButtons()
    {
        foreach (var b in buttons) { Controls.Remove(b); b.Dispose(); } buttons.Clear();
        if (issues != null) { Controls.Remove(issues); issues.Dispose(); issues = null; }
    }
    void ShowIssues(IEnumerable<string> lines)
    {
        issues = new ListBox { Left = 36, Top = 198, Width = 528, Height = 158,
            BackColor = Color.FromArgb(13, 24, 39), ForeColor = Color.FromArgb(208, 226, 238),
            BorderStyle = BorderStyle.FixedSingle, HorizontalScrollbar = true, Font = new Font("Segoe UI", 9) };
        issues.Items.AddRange(lines.Cast<object>().ToArray()); Controls.Add(issues);
    }
    void AddButton(string text, Action action)
    {
        var b = new Button { Text = text, AutoSize = false, Height = 34, Width = 174,
            Top = 532, Left = 24 + buttons.Count * 187, FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(23, 42, 61), ForeColor = Color.FromArgb(205, 234, 244), Cursor = Cursors.Hand };
        b.FlatAppearance.BorderColor = Color.FromArgb(48, 81, 100);
        b.Click += delegate { if (!busy) action(); }; buttons.Add(b); Controls.Add(b);
    }
    async Task Guard(Func<Task> action)
    {
        if (busy) return; busy = true;
        try { await action(); }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            phase = "СТАРТИРАНЕТО Е СПРЯНО";
            Update("Не успяхме да завършим подготовката", ex.Message, progress);
            ClearButtons(); AddButton("Опитай отново", async () => await Guard(Start));
            AddButton("Затвори", Close);
        }
        finally { busy = false; }
    }
    List<string> CheckFiles()
    {
        var missing = new List<string>();
        foreach (var entry in Dict(manifest["files"]))
        {
            string path = Path.GetFullPath(Path.Combine(root, entry.Key.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Невалиден път в пакета.");
            if (!File.Exists(path) || Hash(File.ReadAllBytes(path)) != Convert.ToString(entry.Value)) missing.Add(entry.Key);
        }
        return missing;
    }
    async Task Start()
    {
        ClearButtons(); phase = "ПРОВЕРКА НА ФАЙЛОВЕТЕ";
        Update("Проверка на нужните файлове…", "Проверяваме целостта на това копие", .1f);
        var missing = await Task.Run(() => CheckFiles());
        if (missing.Count > 0)
        {
            phase = "ЛИПСВАЩИ ИЛИ ПРОМЕНЕНИ ФАЙЛОВЕ";
            Update("Нужно е възстановяване на файлове", "Виж точния списък преди изтегляне", .1f);
            ShowIssues(missing);
            AddButton("Покажи файловете", () => MessageBox.Show(this, string.Join("\n", missing), "Файлове за възстановяване", MessageBoxButtons.OK, MessageBoxIcon.Warning));
            string packagePage = Str(manifest, "release_tag").Contains("preview") ? Releases : Releases + "/tag/" + Uri.EscapeDataString(Str(manifest, "release_tag"));
            AddButton("Изтегли пакета", () => OpenRelease(packagePage)); AddButton("Затвори", Close); return;
        }
        phase = "ПРОВЕРКА НА КОМПОНЕНТИТЕ";
        Update("Проверка на Python и библиотеките…", "Самостоятелна среда за тази инсталация", .28f);
        string probe = await Probe();
        if (probe != "READY")
        {
            phase = "НУЖНИ СА ДОПЪЛНИТЕЛНИ КОМПОНЕНТИ";
            Update("Нужно е изтегляне преди стартиране", "Изтеглянето започва само след избор на „Изтегли“", .28f);
            ShowIssues(probe.Split(new[] { '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries));
            AddButton("Виж какво липсва", () => MessageBox.Show(this, probe, "Необходими компоненти"));
            AddButton("Изтегли", async () => await Guard(async () => { ClearButtons(); await Install(); await Start(); }));
            AddButton("Отказ", Close); return;
        }
        await CheckUpdates();
    }
    async Task<string> Probe()
    {
        if (!File.Exists(Python)) return "Липсва самостоятелната Python среда.\nЩе се изтеглят Python 3.13.9 и библиотеките:\n" + string.Join("\n", ArrayValue(manifest["packages"]).Select(Convert.ToString));
        try { return (await Execute(Python, "-I -B \"" + Path.Combine(root, "Tools", "runtime_probe.py") + "\"", 60000)).Trim(); }
        catch (InvalidOperationException ex) { return "Python средата се нуждае от възстановяване.\n" + ex.Message; }
        catch (System.ComponentModel.Win32Exception ex) { return "Python средата се нуждае от възстановяване.\n" + ex.Message; }
    }
    async Task<string> Execute(string executable, string args, int timeout)
    {
        using (var process = new Process())
        {
            process.StartInfo = new ProcessStartInfo(executable, args) { WorkingDirectory = root,
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
            // Never accept a Python installation or startup hook from another copy.
            process.StartInfo.EnvironmentVariables.Remove("PYTHONPATH"); process.StartInfo.EnvironmentVariables.Remove("PYTHONHOME");
            process.StartInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            process.Start();
            var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
            var deadline = Stopwatch.StartNew();
            while (!process.HasExited)
            {
                if (stop.IsCancellationRequested || deadline.ElapsedMilliseconds > timeout)
                {
                    try { process.Kill(); } catch (InvalidOperationException) { }
                    stop.Token.ThrowIfCancellationRequested();
                    throw new TimeoutException("Времето за операцията изтече. Опитай отново.");
                }
                await Task.Delay(100);
            }
            string text = await output, errors = await error;
            if (process.ExitCode != 0) throw new InvalidOperationException("Компонентът не премина проверката.\n" + errors.Substring(0, Math.Min(1200, errors.Length)));
            return text;
        }
    }
    async Task<byte[]> Download(string url)
    {
        using (var client = new WebClient())
        using (stop.Token.Register(client.CancelAsync))
        {
            client.Headers[HttpRequestHeader.UserAgent] = "AstroArchive-Launcher";
            return await client.DownloadDataTaskAsync(new Uri(url));
        }
    }
    static void Extract(byte[] bytes, string target)
    {
        Directory.CreateDirectory(target);
        using (var archive = new ZipArchive(new MemoryStream(bytes)))
            foreach (var entry in archive.Entries)
            {
                string path = Path.GetFullPath(Path.Combine(target, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                if (!path.StartsWith(Path.GetFullPath(target) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Невалиден път в изтегления архив.");
                if (entry.Name.Length == 0) { Directory.CreateDirectory(path); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(path)); entry.ExtractToFile(path, true);
            }
    }
    async Task Install()
    {
        phase = "ИЗТЕГЛЯНЕ НА КОМПОНЕНТИ";
        Update("Изтегляне на Python 3.13.9…", "Официален източник: python.org", .35f);
        string stage = Path.Combine(root, ".runtime", "prepare-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(stage);
        try
        {
            Extract(await Download(PythonURL), stage);
            // The embeddable runtime ignores registry, PATH, user site and other copies.
            File.WriteAllText(Path.Combine(stage, "python313._pth"), "python313.zip\n.\nsite-packages\n", Encoding.ASCII);
            Update("Подготовка на инсталатора за библиотеки…", "Официален източник: pypi.org • pip 25.3", .48f);
            var metadata = Dict(Json.DeserializeObject(Encoding.UTF8.GetString(await Download(PipURL))));
            var wheel = ArrayValue(metadata["urls"]).Select(Dict).Single(x => Str(x, "filename") == "pip-25.3-py3-none-any.whl");
            string url = Str(wheel, "url");
            if (new Uri(url).Scheme != "https" || new Uri(url).Host != "files.pythonhosted.org") throw new InvalidDataException("Непознат източник на библиотека.");
            byte[] data = await Download(url);
            if (Hash(data) != Str(Dict(wheel["digests"]), "sha256")) throw new InvalidDataException("Повреден инсталатор на библиотеки.");
            Extract(data, Path.Combine(stage, "site-packages"));
            Update("Изтегляне и инсталиране на библиотеките…", "Това може да отнеме няколко минути", .6f);
            await Execute(Path.Combine(stage, "python.exe"), "-I -B -m pip --isolated install --disable-pip-version-check --index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir --target \"" + Path.Combine(stage, "site-packages") + "\" -r \"" + Path.Combine(root, "requirements-lock.txt") + "\"", 1200000);
            string check = (await Execute(Path.Combine(stage, "python.exe"), "-I -B \"" + Path.Combine(root, "Tools", "runtime_probe.py") + "\"", 60000)).Trim();
            if (check != "READY") throw new InvalidOperationException(check);
            // Commit only a complete, tested runtime. Keep the old one for recovery.
            if (Directory.Exists(Runtime)) Directory.Move(Runtime, Runtime + ".previous-" + Guid.NewGuid().ToString("N"));
            Directory.Move(stage, Runtime);
        }
        finally
        {
            if (Directory.Exists(stage)) Directory.Delete(stage, true);
        }
    }
    Dictionary<string, object> ReadState()
    { try { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(State)) ?? new Dictionary<string, object>(); } catch (IOException) { return new Dictionary<string, object>(); } catch (ArgumentException) { return new Dictionary<string, object>(); } }
    async Task CheckUpdates()
    {
        string resultNote = "Проверките приключиха";
        phase = "ПРОВЕРКА ЗА НОВА ВЕРСИЯ";
        Update("Търсене на публикувана версия…", "Само издания от официалното хранилище", .82f);
        try
        {
            var request = Download(ReleaseAPI);
            if (await Task.WhenAny(request, Task.Delay(6000)) != request)
                throw new WebException("Проверката за версия надвиши времето за изчакване.");
            var releases = ArrayValue(Json.DeserializeObject(Encoding.UTF8.GetString(await request))).Select(Dict)
                .Where(x => !Convert.ToBoolean(x["draft"]) && x.ContainsKey("published_at") && x["published_at"] != null)
                .OrderByDescending(x => DateTime.Parse(Str(x, "published_at"), null, System.Globalization.DateTimeStyles.RoundtripKind)).ToList();
            // Current release ID/date is embedded at build time, never inferred from tags lexically.
            long currentID = Convert.ToInt64(manifest["release_id"]);
            DateTime built = DateTime.Parse(Str(manifest, "release_cutoff"), null, System.Globalization.DateTimeStyles.RoundtripKind);
            var release = releases.FirstOrDefault(x => Convert.ToInt64(x["id"]) != currentID && Str(x, "tag_name") != Str(manifest, "release_tag") && DateTime.Parse(Str(x, "published_at"), null, System.Globalization.DateTimeStyles.RoundtripKind) > built);
            if (release != null && Str(ReadState(), "skipped_release") != Str(release, "id"))
            {
                string page = Str(release, "html_url"); ValidateReleaseURL(page);
                phase = "ИМА НОВА ВЕРСИЯ";
                Update("Налична е " + Str(release, "name"), "Текущото копие ще остане запазено", .86f);
                AddButton("Изтегли", () => { OpenRelease(page); AddContinue(); });
                AddButton("Пропусни сега", async () => await Guard(() => Launch("Версията е пропусната за това стартиране")));
                AddButton("Пропусни версията", async () => await Guard(async () =>
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(State));
                    string temp = State + "." + Guid.NewGuid().ToString("N") + ".tmp";
                    File.WriteAllText(temp, Json.Serialize(new Dictionary<string, object> { { "skipped_release", Str(release, "id") } }));
                    if (File.Exists(State)) File.Replace(temp, State, null); else File.Move(temp, State);
                    await Launch("Версията е пропусната за това копие");
                })); return;
            }
        }
        catch (WebException)
        { resultNote = "Проверката за обновяване е недостъпна — офлайн старт"; }
        catch (ArgumentException)
        { resultNote = "Невалиден отговор при проверката за версия — стартираме текущото копие"; }
        catch (InvalidOperationException)
        { resultNote = "Невалиден отговор при проверката за версия — стартираме текущото копие"; }
        catch (FormatException)
        { resultNote = "Невалиден отговор при проверката за версия — стартираме текущото копие"; }
        catch (KeyNotFoundException)
        { resultNote = "Непълен отговор при проверката за версия — стартираме текущото копие"; }
        catch (InvalidCastException)
        { resultNote = "Невалиден отговор при проверката за версия — стартираме текущото копие"; }
        await Launch(resultNote);
    }
    void AddContinue()
    { ClearButtons(); AddButton("Стартирай това копие", async () => await Guard(() => Launch("Стартиране на текущата версия"))); AddButton("Затвори", Close); }
    static void ValidateReleaseURL(string url)
    {
        Uri uri;
        if (!Uri.TryCreate(url, UriKind.Absolute, out uri) || uri.Scheme != "https" || uri.Host != "github.com" || !(uri.AbsolutePath == "/simonimonov-spec/astro-archive/releases" || uri.AbsolutePath.StartsWith("/simonimonov-spec/astro-archive/releases/tag/", StringComparison.Ordinal))) throw new InvalidDataException("Невалидна страница за обновяване.");
    }
    static void OpenRelease(string url) { ValidateReleaseURL(url); Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    async Task Launch(string note)
    {
        ClearButtons(); phase = "СТАРТИРАНЕ НА ASTRO ARCHIVE";
        Update("Отваряме твоя архив…", note, .96f);
        string token = Guid.NewGuid().ToString("N");
        string ready = Path.Combine(root, "Backend", "Cache", "data", "launcher-ready-" + token + ".json");
        Directory.CreateDirectory(Path.GetDirectoryName(ready));
        var info = new ProcessStartInfo(Path.Combine(Runtime, "pythonw.exe"), "-I -B \"" + Path.Combine(root, "start.pyw") + "\"") { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true };
        info.EnvironmentVariables.Remove("PYTHONPATH"); info.EnvironmentVariables.Remove("PYTHONHOME");
        info.EnvironmentVariables["ASTRO_ARCHIVE_READY"] = ready;
        info.EnvironmentVariables["ASTRO_ARCHIVE_RELEASE_TAG"] = Str(manifest, "release_tag");
        info.EnvironmentVariables["ASTRO_ARCHIVE_RELEASE_CUTOFF"] = Str(manifest, "release_cutoff");
        using (var process = Process.Start(info))
        {
            var deadline = Stopwatch.StartNew();
            try
            {
                while (!File.Exists(ready))
                {
                    if (process.HasExited) throw new InvalidOperationException("Програмата спря преди отваряне. Виж Backend/Cache/data/startup-error.log.");
                    if (deadline.Elapsed.TotalSeconds > 90) throw new TimeoutException("Програмата не потвърди готовност до 90 секунди. Виж startup-error.log.");
                    stop.Token.ThrowIfCancellationRequested(); await Task.Delay(100);
                }
                Update("Готово", "Тази инсталация е стартирана", 1); await Task.Delay(250); Close();
            }
            finally { if (File.Exists(ready)) File.Delete(ready); }
        }
    }
    int SelfTest()
    {
        try
        {
            if (CheckFiles().Count != 0) return 1;
            ValidateReleaseURL(Releases);
            try { ValidateReleaseURL("https://example.com/releases"); return 2; } catch (InvalidDataException) { }
            string temporary = Path.Combine(root, ".runtime", "test-" + Guid.NewGuid().ToString("N"));
            try
            {
                using (var stream = new MemoryStream())
                {
                    using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, true)) zip.CreateEntry("../escape.txt");
                    try { Extract(stream.ToArray(), temporary); return 3; } catch (InvalidDataException) { }
                }
            }
            finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); }
            return 0;
        }
        catch { return 4; }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e); var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
        double t = clock.Elapsed.TotalSeconds;
        using (var background = new LinearGradientBrush(ClientRectangle, Color.FromArgb(9, 15, 27), Color.FromArgb(15, 29, 43), 70f)) g.FillRectangle(background, ClientRectangle);
        var random = new Random(71);
        for (int i = 0; i < 85; i++)
        {
            int x = random.Next(18, 584), y = random.Next(20, 460);
            using (var star = new SolidBrush(Color.FromArgb(random.Next(35, 110), 177, 214, 235))) g.FillEllipse(star, x, y, i % 6 == 0 ? 2 : 1, i % 6 == 0 ? 2 : 1);
        }
        if (logo != null) g.DrawIcon(logo, new Rectangle(270, 32, 60, 60));
        DrawText(g, "ASTRO ARCHIVE", 108, 23, Color.FromArgb(231, 243, 250), FontStyle.Bold);
        DrawText(g, "ТВОЯТА ВСЕЛЕНА. ПОДРЕДЕНА.", 146, 8, Color.FromArgb(123, 160, 183), FontStyle.Regular);
        float cx = 300, cy = 282, radius = 77;
        using (var pen = new Pen(Color.FromArgb(34, 89, 128, 154), 1)) g.DrawEllipse(pen, cx - radius, cy - radius * .62f, radius * 2, radius * 1.24f);
        for (int r = 33; r > 9; r -= 3) using (var glow = new SolidBrush(Color.FromArgb(5, 255, 182, 66))) g.FillEllipse(glow, cx - r, cy - r, r * 2, r * 2);
        using (var sun = new LinearGradientBrush(new RectangleF(cx-13,cy-13,26,26),Color.FromArgb(255,225,141),Color.FromArgb(244,148,49),80)) g.FillEllipse(sun,cx-13,cy-13,26,26);
        float ex = cx + radius * (float)Math.Cos(t * .7), ey = cy + radius * .62f * (float)Math.Sin(t * .7);
        using (var pen = new Pen(Color.FromArgb(45, 137, 173, 199), 1)) g.DrawEllipse(pen, ex - 22, ey - 15, 44, 30);
        using (var earth = new LinearGradientBrush(new RectangleF(ex-10,ey-10,20,20),Color.FromArgb(102,217,237),Color.FromArgb(38,91,170),55)) g.FillEllipse(earth,ex-10,ey-10,20,20);
        using (var land = new SolidBrush(Color.FromArgb(113,183,145))) { g.FillEllipse(land,ex-7,ey-4,7,5); g.FillEllipse(land,ex+1,ey+1,5,6); }
        float mx = ex + 22 * (float)Math.Cos(t * 2.6), my = ey + 15 * (float)Math.Sin(t * 2.6);
        using (var moon = new SolidBrush(Color.FromArgb(205,214,221))) g.FillEllipse(moon,mx-3,my-3,6,6);
        DrawText(g, phase, 374, 8, Color.FromArgb(102, 196, 210), FontStyle.Bold);
        DrawText(g, status, 401, 14, Color.FromArgb(225, 238, 246), FontStyle.Regular);
        using (var track = new Pen(Color.FromArgb(31, 56, 75), 3)) g.DrawLine(track, 50, 457, 550, 457);
        float head = 50 + 500 * Math.Max(0, Math.Min(1, progress));
        using (var trail = new LinearGradientBrush(new PointF(50,457),new PointF(Math.Max(51,head),457),Color.FromArgb(10,55,155,180),Color.FromArgb(117,223,239)))
        using (var pen = new Pen(trail,3)) g.DrawLine(pen,50,457,head,457);
        for (int i=27;i>0;i--) using(var tail=new SolidBrush(Color.FromArgb((int)(110*(1-i/28f)),105,211,234))) g.FillEllipse(tail,head-i*2,454+i*.045f,5,5);
        using(var comet = new SolidBrush(Color.FromArgb(230,251,255))) g.FillEllipse(comet,head-4,453,8,8);
        using(var font = new Font("Segoe UI",9)) using(var brush = new SolidBrush(Color.FromArgb(141,171,191)))
        using(var format=new StringFormat { Alignment=StringAlignment.Center, Trimming=StringTrimming.EllipsisCharacter })
            g.DrawString(detail,font,brush,new RectangleF(24,480,552,45),format);
    }
    void DrawText(Graphics g, string text, float y, float size, Color color, FontStyle style)
    {
        using(var font=new Font("Segoe UI",size,style)) using(var brush=new SolidBrush(color))
        using(var format=new StringFormat { Alignment=StringAlignment.Center, Trimming=StringTrimming.EllipsisCharacter })
            g.DrawString(text,font,brush,new RectangleF(20,y,560,42),format);
    }
    protected override void Dispose(bool disposing)
    { if (disposing) { timer.Dispose(); stop.Dispose(); if (logo != null) logo.Dispose(); } base.Dispose(disposing); }
}
