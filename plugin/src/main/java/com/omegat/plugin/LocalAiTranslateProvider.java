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
import org.omegat.util.Preferences;

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

    private static final int MAX_FUZZY_MATCHES    = 3;
    private static final int CONTEXT_BEFORE_COUNT = 1;
    private static final int CONTEXT_AFTER_COUNT  = 1;

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
            }
        });
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
        } catch (Exception ignored) {}

        String currentFilePath = currentEntry != null ? currentEntry.getKey().file : null;
        String styleRules = loadProjectStyleRules();

        String requestBody = buildTranslateJson(
            text, sLang.getLanguage(), tLang.getLanguage(),
            currentFilePath, glossaryEntries, matchesToSend, contextBefore, contextAfter,
            styleRules
        );

        String response = httpPost(SERVICE_URL, requestBody, 30_000);
        String translated = extractStringField(response, "translated_text");
        if (translated == null) throw new Exception("'translated_text' not found in service response");
        return translated;
    }

    // ── translate() helpers ───────────────────────────────────────────────────

    /** Reads {projectRoot}/ai_style_rules.txt if present; null if absent (service falls back to its global setting). */
    static String loadProjectStyleRules() {
        try {
            String projectRoot = Core.getProject().getProjectProperties().getProjectRoot();
            Path rulesFile = Paths.get(projectRoot, "ai_style_rules.txt");
            if (!Files.isRegularFile(rulesFile)) return null;
            return Files.readString(rulesFile, StandardCharsets.UTF_8);
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

    // ── JSON serialisation ────────────────────────────────────────────────────

    private static String buildTranslateJson(
            String text, String srcLang, String tgtLang, String filePath,
            List<GlossaryEntry> glossary, List<NearString> matches,
            List<String[]> contextBefore, List<String[]> contextAfter,
            String styleRules) {

        StringBuilder sb = new StringBuilder("{");
        sb.append("\"source_text\":").append(quoted(text)).append(",");
        sb.append("\"source_lang\":").append(quoted(srcLang)).append(",");
        sb.append("\"target_lang\":").append(quoted(tgtLang)).append(",");
        if (filePath != null) sb.append("\"file_path\":").append(quoted(filePath)).append(",");
        if (styleRules != null) sb.append("\"style_rules\":").append(quoted(styleRules)).append(",");

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

        private final Set<String> processedThisSession = ConcurrentHashMap.newKeySet();

        @Override
        public void onNewFile(String activeFileName) {
            if (activeFileName == null) return;
            if (processedThisSession.contains(activeFileName)) return;
            if (!Core.getProject().isProjectLoaded()) return;
            processedThisSession.add(activeFileName);
            new Thread(() -> checkAndPrompt(activeFileName), "glossary-checker").start();
            new Thread(() -> ensureFileSummary(activeFileName), "summary-generator").start();
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
            } catch (Exception ignored) {}
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
            } catch (Exception ignored) {}
        }

        private static String buildSummaryRequestJson(String filePath, List<String> sourceStrings,
                                                       String srcLang, String tgtLang) {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"file_path\":").append(quoted(filePath)).append(",");
            sb.append("\"source_lang\":").append(quoted(srcLang)).append(",");
            sb.append("\"target_lang\":").append(quoted(tgtLang)).append(",");
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
    }
}
