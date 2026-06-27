package com.omegat.plugin;

import org.omegat.core.Core;
import org.omegat.core.CoreEvents;
import org.omegat.core.data.IProject;
import org.omegat.core.data.SourceTextEntry;
import org.omegat.core.data.TMXEntry;
import org.omegat.core.events.IEntryEventListener;
import org.omegat.core.events.IProjectEventListener;
import org.omegat.core.machinetranslators.BaseTranslate;
import org.omegat.core.matching.NearString;
import org.omegat.gui.glossary.GlossaryEntry;
import org.omegat.util.Language;
import org.omegat.util.Log;
import org.omegat.util.Preferences;

import javax.swing.JMenuItem;
import javax.swing.JOptionPane;
import javax.swing.SwingUtilities;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * OmegaT MT plugin that forwards segments to a local AI service.
 *
 * Also listens for file-change events to offer glossary extraction via Termium / OQLF.
 *
 * Install: copy the built JAR to the OmegaT plugins folder:
 *   macOS:   ~/Library/Preferences/OmegaT/plugins/
 *   Windows: %APPDATA%\OmegaT\plugins\
 *   Linux:   ~/.omegat/plugins/
 * Build:   mvn -f plugin/pom.xml package
 */
public class LocalAiTranslateProvider extends BaseTranslate {

    private static final String DEFAULT_SERVICE_BASE_URL = "http://localhost:8000";

    // Base URL for the AI translation service. Override via the "ai_translation_service_url"
    // key in OmegaT preferences (omegat.prefs) if the service runs on a different host/port.
    // Preferences is only initialized once OmegaT's full runtime is bootstrapped, so fall back
    // to the default outside that context (e.g. plain unit tests run this class in isolation).
    private static final String SERVICE_BASE_URL = resolveServiceBaseUrl();

    static String resolveServiceBaseUrl() {
        try {
            return Preferences.getPreferenceDefault("ai_translation_service_url", DEFAULT_SERVICE_BASE_URL);
        } catch (Throwable e) {
            return DEFAULT_SERVICE_BASE_URL;
        }
    }

    private static final String SERVICE_URL          = SERVICE_BASE_URL + "/translate";
    private static final String GLOSSARY_URL         = SERVICE_BASE_URL + "/prepare-glossary";
    private static final String GLOSSARY_STATUS_URL  = SERVICE_BASE_URL + "/glossary/status";
    private static final String GLOSSARY_DEFER_URL   = SERVICE_BASE_URL + "/glossary/defer";
    private static final String FILE_SUMMARY_URL     = SERVICE_BASE_URL + "/file-summary/generate";
    private static final String BATCH_TRANSLATE_URL  = SERVICE_BASE_URL + "/batch-translate";

    private static final int MAX_FUZZY_MATCHES    = 3;
    private static final int CONTEXT_BEFORE_COUNT = 1;
    private static final int CONTEXT_AFTER_COUNT  = 1;

    // Prefix so the plugin's lines are greppable in OmegaT's shared log file.
    private static final String LOG_PREFIX = "AI Translation Assistant: ";

    // Project roots whose style-rules outcome we've already logged, so we log once per
    // project per session instead of once per translated segment (translate() runs per segment).
    private static final Set<String> styleRulesLogged = ConcurrentHashMap.newKeySet();

    // Project roots for which the near-miss style-rules popup has already been shown this session.
    private static final Set<String> nearMissShown = ConcurrentHashMap.newKeySet();

    // Template written by the "Create AI style rules file" menu action.
    private static final String STYLE_RULES_TEMPLATE =
        "# Style rules for AI translation — one rule per line.\n"
        + "# Lines starting with # are ignored.\n"
        + "#\n"
        + "# Add one rule per line below. Example rules:\n"
        + "#   Use inclusive gender forms where applicable.\n"
        + "#   Follow the client's preferred terminology for technical terms.\n"
        + "#   Use formal register in all target text.\n"
        + "\n";

    // ── Plugin lifecycle ──────────────────────────────────────────────────────

    /** Called by OmegaT at startup to register this provider and event listeners. */
    public static void loadPlugins() {
        Core.registerMachineTranslationClass(LocalAiTranslateProvider.class);

        GlossaryExtractionListener listener = new GlossaryExtractionListener();
        CoreEvents.registerEntryEventListener(listener);
        CoreEvents.registerProjectChangeListener(eventType -> {
            if (eventType == IProjectEventListener.PROJECT_CHANGE_TYPE.LOAD) {
                // Safety net: onNewFile may fire before isProjectLoaded() returns true;
                // re-check after the project LOAD event to cover that timing edge case.
                listener.checkCurrentFile();
                new Thread(LocalAiTranslateProvider::checkStyleRulesNearMiss, "style-rules-checker").start();
            }
        });
        SwingUtilities.invokeLater(LocalAiTranslateProvider::registerStyleRulesMenuItem);
    }

    /** Called by OmegaT at shutdown. */
    public static void unloadPlugins() {}

    // ── MT translation ────────────────────────────────────────────────────────

    @Override
    public String getName() { return "AI Translation Assistant"; }

    @Override
    public String getPreferenceName() { return "allow_ai_translation_assistant"; }

    @Override
    protected String translate(Language sLang, Language tLang, String text) throws Exception {
        // ── Glossary matches ─────────────────────────────────────────────────
        List<GlossaryEntry> glossaryEntries = Collections.emptyList();
        SourceTextEntry currentEntry = null;
        try {
            currentEntry = Core.getEditor().getCurrentEntry();
            if (currentEntry != null) {
                glossaryEntries = Core.getGlossaryManager().searchSourceMatches(currentEntry);
            }
        } catch (Exception ignored) {}

        // ── Surrounding context ──────────────────────────────────────────────
        // Use object identity (==) to find the current entry — avoids any path/key
        // format mismatch when iterating projectFiles.
        List<String[]> contextBefore = new ArrayList<>();
        List<String[]> contextAfter  = new ArrayList<>();
        if (currentEntry != null) {
            outer:
            for (IProject.FileInfo fi : Core.getProject().getProjectFiles()) {
                List<SourceTextEntry> entries = fi.entries;
                for (int i = 0; i < entries.size(); i++) {
                    if (entries.get(i) == currentEntry) {
                        for (int j = Math.max(0, i - CONTEXT_BEFORE_COUNT); j < i; j++) {
                            contextBefore.add(contextSegment(entries.get(j)));
                        }
                        for (int j = i + 1; j <= Math.min(entries.size() - 1, i + CONTEXT_AFTER_COUNT); j++) {
                            contextAfter.add(contextSegment(entries.get(j)));
                        }
                        break outer;
                    }
                }
            }
        }

        // ── Fuzzy matches with change-detection ──────────────────────────────
        // translate() is called concurrently with OmegaT loading TM matches for the
        // new segment. The internal `matches` field may still contain the previous
        // segment's results at call time.
        //
        // Strategy: record a fingerprint of the initial state (sorted source strings),
        // then poll until the fingerprint changes (new results arrived), then collect
        // once the list stabilises (size doesn't grow across two consecutive reads).
        List<NearString> matchesToSend = new ArrayList<>();
        try {
            Object matcher = Core.getMatcher();
            java.lang.reflect.Field f = getMatchesField(matcher);
            if (f != null) {
                @SuppressWarnings("unchecked")
                String initialFp = fingerprint((List<NearString>) f.get(matcher));
                boolean changed = false;
                int prevSz = -1;
                for (int attempt = 0; attempt < 12; attempt++) {
                    Thread.sleep(200);
                    @SuppressWarnings("unchecked")
                    List<NearString> all = (List<NearString>) f.get(matcher);
                    if (all == null) continue;
                    if (!changed && !fingerprint(all).equals(initialFp)) changed = true;
                    if (changed) {
                        int sz = (int) all.stream()
                            .filter(ns -> ns.scores != null && ns.scores.length > 0)
                            .count();
                        if (sz > 0 && sz == prevSz) {
                            for (NearString ns : all) {
                                if (ns.scores != null && ns.scores.length > 0) {
                                    matchesToSend.add(ns);
                                    if (matchesToSend.size() >= MAX_FUZZY_MATCHES) break;
                                }
                            }
                            break;
                        }
                        prevSz = sz;
                    }
                }
            }
        } catch (Exception e) {
            Log.log(LOG_PREFIX + "fuzzy-match reflection failed — no fuzzy matches will be sent: " + e.getMessage());
        }

        String currentFilePath = currentEntry != null ? currentEntry.getKey().file : null;
        String styleRules = loadProjectStyleRules();
        String projectId = currentProjectId();

        // Per-segment intentionally — the payload summary is what you need when diagnosing
        // "why weren't style rules / glossary / fuzzy matches applied to this segment?"
        Log.log(LOG_PREFIX + "translate request: glossary=" + glossaryEntries.size() + " terms, "
            + "fuzzy=" + matchesToSend.size() + " matches, "
            + "context_before=" + contextBefore.size() + ", context_after=" + contextAfter.size() + ", "
            + "style_rules=" + (styleRules != null ? "yes (" + styleRules.length() + " chars)" : "no") + ", "
            + "project_id=" + (projectId != null ? "set" : "none"));

        String requestBody = buildTranslateJson(
            text, sLang.getLanguage(), tLang.getLanguage(),
            currentFilePath, glossaryEntries, matchesToSend, contextBefore, contextAfter,
            styleRules, projectId
        );

        String response = httpPost(SERVICE_URL, requestBody, 30_000);
        String translated = extractStringField(response, "translated_text");
        if (translated == null) throw new Exception("'translated_text' not found in service response");
        List<String> qaFindings = extractStringArray(response, "qa_findings");
        for (String finding : qaFindings) {
            Log.log(LOG_PREFIX + "QA correction: " + finding);
        }
        return translated;
    }

    // ── translate() helpers ───────────────────────────────────────────────────

    /**
     * Reads {projectRoot}/ai_style_rules.txt if present; null if absent (service falls back to its
     * global setting). Logs the resolved path and outcome once per project per session so a missing
     * or misplaced file is visible in OmegaT's log rather than failing silently.
     */
    static String loadProjectStyleRules() {
        String projectRoot;
        try {
            projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
        } catch (Exception e) {
            // No project loaded (e.g. outside the OmegaT runtime) — nothing to read, not an error.
            return null;
        }

        Path rulesFile = Paths.get(projectRoot, "ai_style_rules.txt");
        boolean firstTimeForProject = styleRulesLogged.add(projectRoot);
        try {
            if (!Files.isRegularFile(rulesFile)) {
                if (firstTimeForProject) {
                    Log.log(LOG_PREFIX + "no project style rules file at " + rulesFile
                        + " — falling back to the service's global STYLE_RULES_PATH (or none). "
                        + "If you meant to use project style rules, place the file at exactly that path.");
                }
                return null;
            }
            String content = Files.readString(rulesFile, StandardCharsets.UTF_8);
            if (firstTimeForProject) {
                Log.log(LOG_PREFIX + "loaded project style rules from " + rulesFile
                    + " (" + content.length() + " chars)");
            }
            return content;
        } catch (Exception e) {
            // File exists but couldn't be read (permissions, encoding) — a real error, surface it.
            if (firstTimeForProject) {
                Log.log(LOG_PREFIX + "failed to read project style rules at " + rulesFile);
            }
            Log.log(e);
            return null;
        }
    }

    /**
     * On project LOAD, scans the project root for a file that looks like a style-rules file
     * but is wrongly named. If found, offers a rename popup (once per project per session).
     * Also flags the canonical file if it exists but can't be read.
     */
    static void checkStyleRulesNearMiss() {
        String projectRoot;
        try {
            projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
        } catch (Exception e) {
            return; // No project loaded — not an error
        }

        if (!nearMissShown.add(projectRoot)) return; // Already shown this session

        Path root = Paths.get(projectRoot);
        Path canonical = root.resolve("ai_style_rules.txt");

        if (Files.exists(canonical)) {
            if (!Files.isReadable(canonical)) {
                SwingUtilities.invokeLater(() -> JOptionPane.showMessageDialog(null,
                    "Found \"ai_style_rules.txt\" in this project but it can't be read.\n"
                    + "Check file permissions at:\n" + canonical,
                    "AI Translation Assistant: Style Rules", JOptionPane.WARNING_MESSAGE));
            }
            return;
        }

        Path nearMiss = findNearMissStyleRulesFile(root);
        if (nearMiss == null) return;

        String nearMissName = nearMiss.getFileName().toString();
        SwingUtilities.invokeLater(() -> {
            int choice = JOptionPane.showConfirmDialog(null,
                "Found \"" + nearMissName + "\" in your project folder.\n"
                + "Style rules only load from a file named exactly \"ai_style_rules.txt\".\n"
                + "Rename it now?",
                "AI Translation Assistant: Style Rules",
                JOptionPane.YES_NO_OPTION, JOptionPane.WARNING_MESSAGE);
            if (choice == JOptionPane.YES_OPTION) {
                try {
                    Files.move(nearMiss, canonical);
                    styleRulesLogged.remove(projectRoot); // force re-log on next translation
                    JOptionPane.showMessageDialog(null,
                        "Renamed to \"ai_style_rules.txt\".\n"
                        + "Style rules will be used from the next translation.",
                        "AI Translation Assistant", JOptionPane.INFORMATION_MESSAGE);
                } catch (Exception ex) {
                    JOptionPane.showMessageDialog(null,
                        "Could not rename the file: " + ex.getMessage(),
                        "AI Translation Assistant", JOptionPane.ERROR_MESSAGE);
                }
            }
        });
    }

    /**
     * Returns the first file in projectRoot whose name contains "style" and "rule"
     * (case-insensitive) but is neither the canonical "ai_style_rules.txt" nor the
     * shipped template "ai_style_rules.example.txt". Pure and side-effect-free — safe
     * to unit-test without the OmegaT runtime.
     */
    static Path findNearMissStyleRulesFile(Path projectRoot) {
        java.io.File[] files = projectRoot.toFile().listFiles();
        if (files == null) return null;
        for (java.io.File f : files) {
            if (!f.isFile()) continue;
            String name = f.getName();
            String lower = name.toLowerCase();
            if (name.equals("ai_style_rules.txt")) continue;
            if (name.equals("ai_style_rules.example.txt")) continue;
            if (lower.contains("style") && lower.contains("rule")) return f.toPath();
        }
        return null;
    }

    private static void registerStyleRulesMenuItem() {
        try {
            JMenuItem item = new JMenuItem("Create AI style rules file for this project");
            item.addActionListener(e -> onCreateStyleRulesAction());
            Core.getMainWindow().getMainMenu().getToolsMenu().add(item);
        } catch (Throwable t) {
            // OmegaT menu API unavailable (pre-init or test context) — skip silently
        }
    }

    /** Menu action: creates ai_style_rules.txt from the built-in template in the current project. */
    static void onCreateStyleRulesAction() {
        String projectRoot;
        try {
            projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
        } catch (Exception e) {
            JOptionPane.showMessageDialog(null,
                "No project is open. Please open an OmegaT project first.",
                "AI Translation Assistant", JOptionPane.WARNING_MESSAGE);
            return;
        }

        Path rulesFile = Paths.get(projectRoot, "ai_style_rules.txt");
        if (Files.isRegularFile(rulesFile)) {
            JOptionPane.showMessageDialog(null,
                "\"ai_style_rules.txt\" already exists in this project.\nLocation: " + rulesFile,
                "AI Translation Assistant", JOptionPane.INFORMATION_MESSAGE);
            return;
        }

        try {
            Files.writeString(rulesFile, STYLE_RULES_TEMPLATE, StandardCharsets.UTF_8);
            styleRulesLogged.remove(projectRoot); // force re-log when next translation loads it
            JOptionPane.showMessageDialog(null,
                "Created \"ai_style_rules.txt\" in your project folder.\n"
                + "Add one style rule per line — lines starting with # are comments.\n\n"
                + "Location: " + rulesFile,
                "AI Translation Assistant", JOptionPane.INFORMATION_MESSAGE);
        } catch (Exception ex) {
            JOptionPane.showMessageDialog(null,
                "Could not create the file: " + ex.getMessage(),
                "AI Translation Assistant", JOptionPane.ERROR_MESSAGE);
        }
    }

    /**
     * Opaque per-project key sent to the service so it can partition state (glossary/summary
     * caches) across OmegaT projects — never a filesystem path, just a stable hash of one.
     * Null outside the OmegaT runtime (no project loaded).
     */
    static String currentProjectId() {
        try {
            String projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
            java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(projectRoot.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte b : hash) hex.append(String.format("%02x", b));
            return hex.substring(0, 16);
        } catch (Exception e) {
            return null;
        }
    }

    /** [source, translation_or_null] pair for context serialisation. */
    private static String[] contextSegment(SourceTextEntry ste) {
        String translation = null;
        try {
            TMXEntry info = Core.getProject().getTranslationInfo(ste);
            if (info != null && info.isTranslated() && info.translation != null) {
                translation = info.translation;
            }
        } catch (Exception ignored) {}
        return new String[]{ ste.getSrcText(), translation };
    }

    /** Walks the class hierarchy to find the private `matches` field on the matcher. */
    private static java.lang.reflect.Field getMatchesField(Object matcher) {
        Class<?> cls = matcher.getClass();
        while (cls != null) {
            try {
                java.lang.reflect.Field f = cls.getDeclaredField("matches");
                f.setAccessible(true);
                return f;
            } catch (NoSuchFieldException e) {
                cls = cls.getSuperclass();
            }
        }
        return null;
    }

    /** Sorted source strings joined by "|" — stable identity for a match list. */
    static String fingerprint(List<NearString> list) {
        if (list == null || list.isEmpty()) return "";
        return list.stream()
            .filter(ns -> ns.scores != null && ns.scores.length > 0)
            .map(ns -> ns.source)
            .sorted()
            .collect(Collectors.joining("|"));
    }

    // ── Batch context assembly (OMP-026) ──────────────────────────────────────

    /**
     * Returns the number of untranslated entries in the given file.
     * Returns 0 outside the OmegaT runtime or when all entries are already translated.
     */
    static int countUntranslatedEntries(String filePath) {
        try {
            int count = 0;
            for (IProject.FileInfo fi : Core.getProject().getProjectFiles()) {
                if (fi.filePath.equals(filePath)) {
                    for (SourceTextEntry ste : fi.entries) {
                        try {
                            TMXEntry info = Core.getProject().getTranslationInfo(ste);
                            if (info == null || !info.isTranslated()) count++;
                        } catch (Exception ignored) {}
                    }
                    break;
                }
            }
            return count;
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * Builds a /batch-translate JSON body for all untranslated segments in the given file.
     * Each segment includes glossary matches and context neighbors; fuzzy matches are
     * omitted (OmegaT's matcher is async and tied to active-segment navigation).
     * Returns null when the project is not loaded or no untranslated segments exist.
     */
    static String buildBatchRequestJson(String filePath, String srcLang, String tgtLang,
                                         String styleRules, String projectId) {
        List<SourceTextEntry> allEntries = new ArrayList<>();
        try {
            for (IProject.FileInfo fi : Core.getProject().getProjectFiles()) {
                if (fi.filePath.equals(filePath)) {
                    allEntries.addAll(fi.entries);
                    break;
                }
            }
        } catch (Exception e) {
            return null;
        }
        if (allEntries.isEmpty()) return null;

        List<String> segmentJsons = new ArrayList<>();
        for (int i = 0; i < allEntries.size(); i++) {
            SourceTextEntry entry = allEntries.get(i);
            try {
                TMXEntry info = Core.getProject().getTranslationInfo(entry);
                if (info != null && info.isTranslated()) continue;
            } catch (Exception ignored) {}

            List<String[]> ctxBefore = new ArrayList<>();
            List<String[]> ctxAfter  = new ArrayList<>();
            if (i > 0) ctxBefore.add(contextSegment(allEntries.get(i - 1)));
            if (i < allEntries.size() - 1) ctxAfter.add(contextSegment(allEntries.get(i + 1)));

            List<GlossaryEntry> glossary = Collections.emptyList();
            try {
                glossary = Core.getGlossaryManager().searchSourceMatches(entry);
            } catch (Exception ignored) {}

            segmentJsons.add(buildTranslateJson(
                entry.getSrcText(), srcLang, tgtLang, filePath,
                glossary, Collections.emptyList(),
                ctxBefore, ctxAfter, styleRules, projectId
            ));
        }
        if (segmentJsons.isEmpty()) return null;
        return buildBatchJson(segmentJsons);
    }

    /** Wraps pre-built segment JSON strings into a /batch-translate request body. */
    static String buildBatchJson(List<String> segmentJsons) {
        StringBuilder sb = new StringBuilder("{\"segments\":[");
        for (int i = 0; i < segmentJsons.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append(segmentJsons.get(i));
        }
        sb.append("]}");
        return sb.toString();
    }

    // ── JSON serialisation ────────────────────────────────────────────────────

    // Package-visible (not private) so unit tests can assert the request JSON it builds,
    // e.g. that style_rules is included when present and omitted when null.
    static String buildTranslateJson(
            String text, String srcLang, String tgtLang, String filePath,
            List<GlossaryEntry> glossary, List<NearString> matches,
            List<String[]> contextBefore, List<String[]> contextAfter,
            String styleRules, String projectId) {

        StringBuilder sb = new StringBuilder("{");
        sb.append("\"source_text\":").append(quoted(text)).append(",");
        sb.append("\"source_lang\":").append(quoted(srcLang)).append(",");
        sb.append("\"target_lang\":").append(quoted(tgtLang)).append(",");
        if (filePath != null) sb.append("\"file_path\":").append(quoted(filePath)).append(",");
        if (styleRules != null) sb.append("\"style_rules\":").append(quoted(styleRules)).append(",");
        if (projectId != null) sb.append("\"project_id\":").append(quoted(projectId)).append(",");

        sb.append("\"context_before\":[");
        for (int i = 0; i < contextBefore.size(); i++) {
            if (i > 0) sb.append(",");
            appendContextSegment(sb, contextBefore.get(i));
        }
        sb.append("],");

        sb.append("\"context_after\":[");
        for (int i = 0; i < contextAfter.size(); i++) {
            if (i > 0) sb.append(",");
            appendContextSegment(sb, contextAfter.get(i));
        }
        sb.append("],");

        sb.append("\"glossary\":[");
        for (int i = 0; i < glossary.size(); i++) {
            if (i > 0) sb.append(",");
            GlossaryEntry e = glossary.get(i);
            String[] targets = e.getLocTerms(true);
            String primary = (targets != null && targets.length > 0) ? targets[0] : null;
            sb.append("{\"source\":").append(quoted(e.getSrcText()));
            sb.append(",\"target\":").append(quoted(primary));
            String[] comments = e.getComments();
            if (comments != null) {
                StringBuilder commentStr = new StringBuilder();
                for (String c : comments) {
                    if (c != null && !c.isBlank()) {
                        if (commentStr.length() > 0) commentStr.append("; ");
                        commentStr.append(c.strip());
                    }
                }
                if (commentStr.length() > 0) sb.append(",\"comment\":").append(quoted(commentStr.toString()));
            }
            sb.append("}");
        }
        sb.append("],");

        sb.append("\"fuzzy_matches\":[");
        for (int i = 0; i < matches.size(); i++) {
            if (i > 0) sb.append(",");
            NearString ns = matches.get(i);
            sb.append("{");
            sb.append("\"source\":").append(quoted(ns.source)).append(",");
            sb.append("\"target\":").append(quoted(ns.translation)).append(",");
            sb.append("\"score\":").append(ns.scores[0].score).append(",");
            sb.append("\"score_no_stem\":").append(ns.scores[0].scoreNoStem).append(",");
            sb.append("\"adjusted_score\":").append(ns.scores[0].adjustedScore).append(",");
            sb.append("\"match_source\":").append(quoted(ns.comesFrom.name()));
            if (ns.projs != null && ns.projs.length > 0) sb.append(",\"project\":").append(quoted(ns.projs[0]));
            sb.append("}");
        }
        sb.append("]");

        sb.append("}");
        return sb.toString();
    }

    private static void appendContextSegment(StringBuilder sb, String[] seg) {
        sb.append("{\"source\":").append(quoted(seg[0]));
        if (seg[1] != null) sb.append(",\"translation\":").append(quoted(seg[1]));
        sb.append("}");
    }

    // ── HTTP / JSON utilities (package-visible for tests) ─────────────────────

    static String httpPost(String url, String body, int readTimeoutMs) throws Exception {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setDoOutput(true);
        conn.setFixedLengthStreamingMode(bytes.length);
        conn.setConnectTimeout(5_000);
        conn.setReadTimeout(readTimeoutMs);
        try (OutputStream os = conn.getOutputStream()) { os.write(bytes); }
        if (conn.getResponseCode() != 200) throw new Exception("HTTP " + conn.getResponseCode());
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(
                new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }

    static String extractStringField(String json, String fieldName) {
        String marker = "\"" + fieldName + "\":\"";
        int start = json.indexOf(marker);
        if (start < 0) return null;
        start += marker.length();
        StringBuilder result = new StringBuilder();
        int i = start;
        while (i < json.length()) {
            char c = json.charAt(i);
            if (c == '"') break;
            if (c == '\\' && i + 1 < json.length()) {
                char esc = json.charAt(i + 1);
                switch (esc) {
                    case '"':  result.append('"');  i += 2; continue;
                    case '\\': result.append('\\'); i += 2; continue;
                    case 'n':  result.append('\n'); i += 2; continue;
                    case 'r':  result.append('\r'); i += 2; continue;
                    case 't':  result.append('\t'); i += 2; continue;
                    case 'u':
                        if (i + 5 < json.length()) {
                            result.append((char) Integer.parseInt(json.substring(i + 2, i + 6), 16));
                            i += 6; continue;
                        }
                }
            }
            result.append(c);
            i++;
        }
        return result.toString();
    }

    static List<String> extractStringArray(String json, String fieldName) {
        List<String> result = new ArrayList<>();
        String marker = "\"" + fieldName + "\":[";
        int start = json.indexOf(marker);
        if (start < 0) return result;
        int i = start + marker.length();
        while (i < json.length()) {
            char c = json.charAt(i);
            if (c == ']') break;
            if (c == '"') {
                i++;
                StringBuilder sb = new StringBuilder();
                while (i < json.length()) {
                    char sc = json.charAt(i);
                    if (sc == '"') { i++; break; }
                    if (sc == '\\' && i + 1 < json.length()) {
                        char esc = json.charAt(i + 1);
                        switch (esc) {
                            case '"':  sb.append('"');  i += 2; continue;
                            case '\\': sb.append('\\'); i += 2; continue;
                            case 'n':  sb.append('\n'); i += 2; continue;
                            case 'r':  sb.append('\r'); i += 2; continue;
                            case 't':  sb.append('\t'); i += 2; continue;
                            case 'u':
                                if (i + 5 < json.length()) {
                                    sb.append((char) Integer.parseInt(json.substring(i + 2, i + 6), 16));
                                    i += 6; continue;
                                }
                        }
                    }
                    sb.append(sc);
                    i++;
                }
                result.add(sb.toString());
            } else {
                i++;
            }
        }
        return result;
    }

    static int extractIntField(String json, String fieldName) {
        String marker = "\"" + fieldName + "\":";
        int start = json.indexOf(marker);
        if (start < 0) return 0;
        int i = start + marker.length();
        StringBuilder num = new StringBuilder();
        while (i < json.length() && Character.isDigit(json.charAt(i))) num.append(json.charAt(i++));
        try { return Integer.parseInt(num.toString()); } catch (NumberFormatException e) { return 0; }
    }

    /** Counts the number of result entries in a /batch-translate response that were served from cache. */
    static int countFromCacheInBatchResponse(String json) {
        int count = 0, idx = 0;
        String marker = "\"from_cache\":true";
        while ((idx = json.indexOf(marker, idx)) >= 0) { count++; idx += marker.length(); }
        return count;
    }

    static boolean extractBooleanField(String json, String fieldName) {
        String marker = "\"" + fieldName + "\":";
        int start = json.indexOf(marker);
        if (start < 0) return false;
        return json.startsWith("true", start + marker.length());
    }

    static String quoted(String value) {
        if (value == null) return "null";
        return '"' + value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            + '"';
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Glossary extraction
    // ─────────────────────────────────────────────────────────────────────────

    static class GlossaryExtractionListener implements IEntryEventListener {

        private final Set<String> processedThisSession   = ConcurrentHashMap.newKeySet();
        private final Set<String> batchCheckedThisSession = ConcurrentHashMap.newKeySet();

        @Override
        public void onNewFile(String activeFileName) {
            if (activeFileName == null) return;
            if (!Core.getProject().isProjectLoaded()) return;
            if (!processedThisSession.contains(activeFileName)) {
                processedThisSession.add(activeFileName);
                new Thread(() -> checkAndPrompt(activeFileName), "glossary-checker").start();
                new Thread(() -> ensureFileSummary(activeFileName), "summary-generator").start();
            }
            if (!batchCheckedThisSession.contains(activeFileName)) {
                batchCheckedThisSession.add(activeFileName);
                new Thread(() -> checkAndPromptBatch(activeFileName), "batch-pretranslate-checker").start();
            }
        }

        @Override
        public void onEntryActivated(SourceTextEntry newEntry) {}

        void checkCurrentFile() {
            try {
                SourceTextEntry ste = Core.getEditor().getCurrentEntry();
                if (ste == null) return;
                String file = ste.getKey().file;
                if (file != null) onNewFile(file);
            } catch (Exception ignored) {}
        }

        private void checkAndPrompt(String filePath) {
            try {
                if (!Core.getProject().isProjectLoaded()) return;

                List<String> sourceStrings = collectSourceStrings(filePath);
                if (sourceStrings.isEmpty()) return;

                String srcLang = Core.getProject().getProjectProperties()
                    .getSourceLanguage().getLanguage();
                String tgtLang = Core.getProject().getProjectProperties()
                    .getTargetLanguage().getLanguage();

                if (!checkGlossaryStatus(sourceStrings, srcLang, tgtLang)) return;

                SwingUtilities.invokeLater(() -> {
                    String fileName = Paths.get(filePath).getFileName().toString();
                    int choice = JOptionPane.showConfirmDialog(
                        null,
                        "Extract glossary terms from \"" + fileName + "\"?\n"
                        + "Termium and OQLF will be searched for authoritative terminology.\n"
                        + "Suggestions will be saved to glossary/pending_glossary.txt for your review.",
                        "Glossary Extraction",
                        JOptionPane.YES_NO_OPTION,
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (choice == JOptionPane.YES_OPTION) {
                        new Thread(
                            () -> runExtraction(filePath, sourceStrings, srcLang, tgtLang),
                            "glossary-extractor"
                        ).start();
                    } else {
                        // Record refusal so the popup re-appears next OmegaT session.
                        new Thread(
                            () -> deferGlossary(sourceStrings, srcLang, tgtLang, filePath),
                            "glossary-defer"
                        ).start();
                    }
                });
            } catch (Exception ignored) {}
        }

        private void deferGlossary(List<String> sourceStrings,
                                    String srcLang, String tgtLang, String filePath) {
            try {
                String body = buildGlossaryJson(sourceStrings, srcLang, tgtLang, filePath, null);
                httpPost(GLOSSARY_DEFER_URL, body, 5_000);
            } catch (Exception e) {
                Log.log(LOG_PREFIX + "glossary defer failed (non-critical): " + e.getMessage());
            }
        }

        /** Requests a file summary from the service (generates and caches if absent). Silent — no popup. */
        private void ensureFileSummary(String filePath) {
            try {
                if (!Core.getProject().isProjectLoaded()) return;
                List<String> sourceStrings = collectSourceStrings(filePath);
                if (sourceStrings.isEmpty()) return;
                String srcLang = Core.getProject().getProjectProperties().getSourceLanguage().getLanguage();
                String tgtLang = Core.getProject().getProjectProperties().getTargetLanguage().getLanguage();
                String body = buildSummaryRequestJson(filePath, sourceStrings, srcLang, tgtLang);
                httpPost(FILE_SUMMARY_URL, body, 60_000);
            } catch (Exception e) {
                Log.log(LOG_PREFIX + "file-summary request failed for " + filePath + ": " + e.getMessage());
            }
        }

        private static String buildSummaryRequestJson(String filePath, List<String> sourceStrings,
                                                       String srcLang, String tgtLang) {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"file_path\":").append(quoted(filePath)).append(",");
            sb.append("\"source_lang\":").append(quoted(srcLang)).append(",");
            sb.append("\"target_lang\":").append(quoted(tgtLang)).append(",");
            String projectId = currentProjectId();
            if (projectId != null) {
                sb.append("\"project_id\":").append(quoted(projectId)).append(",");
            }
            sb.append("\"source_strings\":[");
            for (int i = 0; i < sourceStrings.size(); i++) {
                if (i > 0) sb.append(",");
                sb.append(quoted(sourceStrings.get(i)));
            }
            sb.append("]}");
            return sb.toString();
        }

        private boolean checkGlossaryStatus(List<String> sourceStrings,
                                             String srcLang, String tgtLang) {
            try {
                String body = buildGlossaryJson(sourceStrings, srcLang, tgtLang, null, null);
                String response = httpPost(GLOSSARY_STATUS_URL, body, 10_000);
                return extractBooleanField(response, "needs_extraction");
            } catch (Exception e) {
                return false; // safe default: don't show popup if service is unreachable
            }
        }

        private void runExtraction(String filePath, List<String> sourceStrings,
                                   String srcLang, String tgtLang) {
            try {
                String projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
                List<String> existingTerms = loadExistingSourceTerms(projectRoot);

                String body = buildGlossaryJson(sourceStrings, srcLang, tgtLang, filePath, existingTerms);
                String responseBody = httpPost(GLOSSARY_URL, body, 120_000);

                List<String[]> suggestions = parseSuggestions(responseBody);
                if (suggestions.isEmpty()) {
                    SwingUtilities.invokeLater(() -> JOptionPane.showMessageDialog(null,
                        "No glossary suggestions found for this file.\n"
                        + "The model found no domain-specific terms worth adding.",
                        "Glossary Extraction Complete", JOptionPane.INFORMATION_MESSAGE));
                    return;
                }

                Path glossaryDir = Paths.get(projectRoot, "glossary");
                Files.createDirectories(glossaryDir);
                Path pendingFile = glossaryDir.resolve("pending_glossary.txt");
                try (BufferedWriter w = Files.newBufferedWriter(pendingFile,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                    for (String[] s : suggestions) {
                        w.write(s[0] + "\t" + s[1] + (s[2] != null ? "\t" + s[2] : ""));
                        w.newLine();
                    }
                }

                final int count = suggestions.size();
                SwingUtilities.invokeLater(() -> JOptionPane.showMessageDialog(null,
                    count + " glossary suggestion" + (count == 1 ? "" : "s")
                    + " added to glossary/pending_glossary.txt.\n"
                    + "Review the file before merging into your main glossary.",
                    "Glossary Extraction Complete", JOptionPane.INFORMATION_MESSAGE));

            } catch (Exception e) {
                SwingUtilities.invokeLater(() -> JOptionPane.showMessageDialog(null,
                    "Glossary extraction failed: " + e.getMessage(),
                    "Glossary Extraction Error", JOptionPane.ERROR_MESSAGE));
            }
        }

        /** Source terms already in the project glossary — prevents the LLM re-suggesting them. */
        private List<String> loadExistingSourceTerms(String projectRoot) {
            List<String> terms = new ArrayList<>();
            java.io.File glossaryDir = new java.io.File(projectRoot, "glossary");
            java.io.File[] files = glossaryDir.listFiles();
            if (files == null) return terms;
            for (java.io.File f : files) {
                String name = f.getName().toLowerCase();
                if (!name.endsWith(".txt") && !name.endsWith(".utf8") && !name.endsWith(".tab")) continue;
                try (BufferedReader r = Files.newBufferedReader(f.toPath(), StandardCharsets.UTF_8)) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        String[] cols = line.split("\t");
                        if (cols.length >= 2 && !cols[0].isBlank()) terms.add(cols[0].trim());
                    }
                } catch (Exception ignored) {}
            }
            return terms;
        }

        private List<String> collectSourceStrings(String filePath) {
            List<String> result = new ArrayList<>();
            for (IProject.FileInfo fi : Core.getProject().getProjectFiles()) {
                if (fi.filePath.equals(filePath)) {
                    for (SourceTextEntry ste : fi.entries) {
                        String src = ste.getSrcText();
                        if (src != null && !src.isBlank()) result.add(src);
                    }
                    break;
                }
            }
            return result;
        }

        /**
         * JSON body for all glossary endpoints.
         * filePath and existingTerms are optional (pass null to omit).
         */
        static String buildGlossaryJson(List<String> sourceStrings, String srcLang, String tgtLang,
                                        String filePath, List<String> existingTerms) {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"source_lang\":").append(quoted(srcLang)).append(",");
            sb.append("\"target_lang\":").append(quoted(tgtLang)).append(",");
            if (filePath != null) {
                sb.append("\"file_path\":").append(quoted(filePath)).append(",");
            }
            String projectId = currentProjectId();
            if (projectId != null) {
                sb.append("\"project_id\":").append(quoted(projectId)).append(",");
            }
            if (existingTerms != null && !existingTerms.isEmpty()) {
                sb.append("\"existing_terms\":[");
                for (int i = 0; i < existingTerms.size(); i++) {
                    if (i > 0) sb.append(",");
                    sb.append(quoted(existingTerms.get(i)));
                }
                sb.append("],");
            }
            sb.append("\"source_strings\":[");
            for (int i = 0; i < sourceStrings.size(); i++) {
                if (i > 0) sb.append(",");
                sb.append(quoted(sourceStrings.get(i)));
            }
            sb.append("]}");
            return sb.toString();
        }

        static List<String[]> parseSuggestions(String json) {
            List<String[]> result = new ArrayList<>();
            int arrStart = json.indexOf("\"suggestions\":[");
            if (arrStart < 0) return result;
            arrStart += "\"suggestions\":[".length();

            int depth = 0, objStart = -1;
            for (int i = arrStart; i < json.length(); i++) {
                char c = json.charAt(i);
                if (c == '{') {
                    if (depth == 0) objStart = i;
                    depth++;
                } else if (c == '}') {
                    depth--;
                    if (depth == 0 && objStart >= 0) {
                        String obj = json.substring(objStart, i + 1);
                        String source  = extractStringField(obj, "source");
                        String target  = extractStringField(obj, "target");
                        String comment = extractStringField(obj, "comment");
                        if (source != null && !source.isEmpty() && target != null && !target.isEmpty()) {
                            result.add(new String[]{ source, target, comment });
                        }
                        objStart = -1;
                    }
                } else if (c == ']' && depth == 0) {
                    break;
                }
            }
            return result;
        }

        // ── Batch pre-translation (OMP-027) ───────────────────────────────────

        private void checkAndPromptBatch(String filePath) {
            try {
                if (!Core.getProject().isProjectLoaded()) return;
                int untranslated = countUntranslatedEntries(filePath);
                if (untranslated == 0) return;

                String fileName = Paths.get(filePath).getFileName().toString();
                String noun = untranslated == 1 ? " segment" : " segments";
                SwingUtilities.invokeLater(() -> {
                    int choice = JOptionPane.showConfirmDialog(
                        null,
                        "Pre-translate " + untranslated + " untranslated" + noun + " in \"" + fileName + "\"?\n"
                        + "Results will be cached for instant retrieval when you reach each segment.",
                        "Batch Pre-Translation",
                        JOptionPane.YES_NO_OPTION,
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (choice == JOptionPane.YES_OPTION) {
                        new Thread(() -> runBatchPreTranslate(filePath, fileName), "batch-pretranslate").start();
                    }
                });
            } catch (Exception ignored) {}
        }

        private void runBatchPreTranslate(String filePath, String fileName) {
            try {
                if (!Core.getProject().isProjectLoaded()) return;
                String srcLang = Core.getProject().getProjectProperties().getSourceLanguage().getLanguage();
                String tgtLang = Core.getProject().getProjectProperties().getTargetLanguage().getLanguage();
                String styleRules = loadProjectStyleRules();
                String projectId  = currentProjectId();

                String body = buildBatchRequestJson(filePath, srcLang, tgtLang, styleRules, projectId);
                if (body == null) {
                    Log.log(LOG_PREFIX + "batch pre-translate: no untranslated segments in " + filePath);
                    return;
                }

                Log.log(LOG_PREFIX + "batch pre-translate: starting for " + filePath);
                String response = httpPost(BATCH_TRANSLATE_URL, body, 300_000);

                int completed  = extractIntField(response, "completed");
                int failed     = extractIntField(response, "failed");
                int fromCache  = countFromCacheInBatchResponse(response);
                int newTx      = completed - fromCache;

                Log.log(LOG_PREFIX + "batch pre-translate complete: "
                    + completed + " done, " + failed + " failed, "
                    + fromCache + " from cache, " + newTx + " new for " + filePath);

                String msg = failed > 0
                    ? completed + " segment" + (completed == 1 ? "" : "s")
                      + " pre-translated (" + failed + " failed — see OmegaT log)."
                    : completed + " segment" + (completed == 1 ? "" : "s")
                      + " pre-translated (" + fromCache + " from cache, " + newTx + " new).";
                SwingUtilities.invokeLater(() ->
                    JOptionPane.showMessageDialog(null, msg,
                        "Batch Pre-Translation: " + fileName, JOptionPane.INFORMATION_MESSAGE));
            } catch (Exception e) {
                Log.log(LOG_PREFIX + "batch pre-translate failed for " + filePath + ": " + e.getMessage());
            }
        }
    }
}
