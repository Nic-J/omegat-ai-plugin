/*
 * ARCHIVED — DO NOT USE
 *
 * This Groovy script was an alternative install path for the OmegaT AI plugin.
 * It was decommissioned because OmegaT 6 builds the MT provider panel before
 * application_startup scripts run, making it impossible to register an MT provider
 * from a Groovy script. With this script and no JAR, "AI Translation Assistant"
 * does not appear in OmegaT at all.
 *
 * Use the JAR plugin instead. See plugin/ and the top-level README.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * Original header preserved below for reference:
 *
 * ai_plugin.groovy — OmegaT AI Translation + Glossary Extraction
 *
 * Install: copy (or symlink) to:
 *   ~/Library/Preferences/OmegaT/script/application_startup/ai_plugin.groovy
 *
 * Before restarting OmegaT, remove the old Java plugin to avoid duplicate
 * event listeners and double popups:
 *   ~/Library/Preferences/OmegaT/plugins/ai-translate-plugin-*.jar
 */

import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import org.omegat.core.Core
import org.omegat.core.CoreEvents
import org.omegat.core.data.SourceTextEntry
import org.omegat.core.events.IEntryEventListener
import org.omegat.core.events.IProjectEventListener
import org.omegat.core.machinetranslators.BaseTranslate
import org.omegat.core.matching.NearString
import org.omegat.gui.glossary.GlossaryEntry
import org.omegat.util.Language

import javax.swing.JOptionPane
import javax.swing.SwingUtilities
import java.net.HttpURLConnection
import java.util.concurrent.ConcurrentHashMap
import java.util.logging.Logger

// ── HTTP + JSON ───────────────────────────────────────────────────────────────

class Util {
    static final String SERVICE_URL         = "http://localhost:8000/translate"
    static final String GLOSSARY_URL        = "http://localhost:8000/prepare-glossary"
    static final String GLOSSARY_STATUS_URL = "http://localhost:8000/glossary/status"
    static final String GLOSSARY_DEFER_URL  = "http://localhost:8000/glossary/defer"

    /** POST a Map/List serialised as JSON; returns the response body. */
    static String httpPost(String url, Object body, int readTimeout = 30_000) {
        byte[] bytes = JsonOutput.toJson(body).getBytes("UTF-8")
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection()
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
        conn.doOutput = true
        conn.setFixedLengthStreamingMode(bytes.length)
        conn.connectTimeout = 5_000
        conn.readTimeout = readTimeout
        conn.outputStream.withStream { it.write(bytes) }
        if (conn.responseCode != 200) throw new IOException("HTTP ${conn.responseCode}")
        conn.inputStream.getText("UTF-8")
    }

    static def parseJson(String text) { new JsonSlurper().parseText(text) }
}

// ── Translation provider ──────────────────────────────────────────────────────

class AiTranslateProvider extends BaseTranslate {

    private static final Logger LOG = Logger.getLogger("AiTranslateProvider")

    @Override
    String getName() { "AI Translation Assistant" }

    @Override
    String getPreferenceName() { "allow_ai_translation_assistant" }

    @Override
    protected String translate(Language sLang, Language tLang, String text) throws Exception {
        // translate() runs on a background thread. We cannot compare `text` to
        // srcText/ns.source because OmegaT may strip inline tags before calling us
        // while srcText and TM sources retain them. Instead:
        //   - context: use currentEntry directly (OmegaT calls us for the current entry)
        //   - fuzzy matches: detect when the matches list *changes* from its initial
        //     state (previous segment's results) and collect the stable new results.

        // ── Glossary matches ─────────────────────────────────────────────────────
        List<GlossaryEntry> glossaryEntries = []
        try {
            SourceTextEntry ste = Core.editor.currentEntry
            if (ste) glossaryEntries = Core.glossaryManager.searchSourceMatches(ste)
        } catch (Exception e) { LOG.warning("glossary lookup failed: ${e}") }

        // ── Fuzzy matches ────────────────────────────────────────────────────────
        // The `matches` field on the matcher is computed asynchronously for the
        // current entry. When translate() is called it may still hold the previous
        // segment's results. We record a fingerprint of the initial state and wait
        // until the list changes, then collect once it stabilises.
        List<NearString> matchesToSend = []
        try {
            Object matcher = Core.matcher
            java.lang.reflect.Field f = null
            Class<?> cls = matcher.class
            while (cls && !f) {
                try      { f = cls.getDeclaredField("matches") }
                catch (NoSuchFieldException ignored) { cls = cls.superclass }
            }
            if (f) {
                f.accessible = true

                // Fingerprint of current matches: sorted sources joined — used to
                // detect when OmegaT refreshes the list for the new segment.
                // No type annotations on the closure: JSR-223 engine rejects them.
                def fp = { list ->
                    list?.findAll { it.scores?.length > 0 }
                        ?.collect { it.source }
                        ?.sort()
                        ?.join("|") ?: ""
                }
                def initialFp = fp(f.get(matcher))

                def changed = false
                int prevSz = -1
                for (int attempt = 0; attempt < 12; attempt++) {
                    Thread.sleep(200)
                    def all = f.get(matcher)
                    if (!all) continue
                    if (!changed && fp(all) != initialFp) changed = true
                    if (changed) {
                        int sz = all.count { it.scores?.length > 0 }
                        if (sz > 0 && sz == prevSz) {
                            all.each { ns ->
                                if (ns.scores?.length > 0 && matchesToSend.size() < 3)
                                    matchesToSend << ns
                            }
                            break
                        }
                        prevSz = sz
                    }
                }
                LOG.info("translate: fuzzy matches=${matchesToSend.size()} changed=${changed}")
            }
        } catch (Exception e) { LOG.warning("fuzzy match poll failed: ${e}") }

        // ── Surrounding context ───────────────────────────────────────────────────
        // Use object identity (is()) rather than path-string or key equality to
        // locate the current entry in projectFiles — avoids any path format mismatch.
        List contextBefore = []
        List contextAfter = []
        try {
            SourceTextEntry targetEntry = Core.editor.currentEntry
            if (targetEntry) {
                List entries = []
                for (def fi in Core.project.projectFiles) {
                    if (fi.entries?.any { it.is(targetEntry) }) {
                        entries = fi.entries
                        break
                    }
                }
                int idx = entries.findIndexOf { it.is(targetEntry) }
                LOG.info("translate: context idx=${idx} of ${entries.size()} entries")
                if (idx >= 0) {
                    for (int i = Math.max(0, idx - 3); i < idx; i++) {
                        def ste = entries[i]
                        def info = Core.project.getTranslationInfo(ste)
                        def seg = [source: ste.srcText]
                        if (info?.isTranslated() && info.translation) seg.translation = info.translation
                        contextBefore << seg
                    }
                    for (int i = idx + 1; i <= Math.min(entries.size() - 1, idx + 2); i++) {
                        def ste = entries[i]
                        def info = Core.project.getTranslationInfo(ste)
                        def seg = [source: ste.srcText]
                        if (info?.isTranslated() && info.translation) seg.translation = info.translation
                        contextAfter << seg
                    }
                }
            } else {
                LOG.warning("translate: currentEntry is null")
            }
        } catch (Exception e) { LOG.warning("context lookup failed: ${e}") }

        def body = [
            source_text:    text,
            source_lang:    sLang.language,
            target_lang:    tLang.language,
            context_before: contextBefore,
            context_after:  contextAfter,
            glossary:      glossaryEntries.collect { GlossaryEntry e ->
                def t = e.getLocTerms(true)
                def obj = [source: e.srcText, target: t ? t[0] : null]
                def cs = e.comments?.grep { it?.trim() }
                if (cs) obj.comment = cs.join("; ")
                obj
            },
            fuzzy_matches: matchesToSend.collect { NearString ns ->
                def obj = [
                    source:         ns.source,
                    target:         ns.translation,
                    score:          ns.scores[0].score,
                    score_no_stem:  ns.scores[0].scoreNoStem,
                    adjusted_score: ns.scores[0].adjustedScore,
                    match_source:   ns.comesFrom.name()
                ]
                if (ns.projs?.length > 0) obj.project = ns.projs[0]
                obj
            }
        ]

        def resp = Util.parseJson(Util.httpPost(Util.SERVICE_URL, body))
        if (!resp.translated_text)
            throw new Exception("'translated_text' not found in service response")
        resp.translated_text as String
    }
}

// ── Glossary extraction ───────────────────────────────────────────────────────

class AiGlossaryPlugin {

    private static final Logger LOG = Logger.getLogger("AiGlossaryPlugin")
    final Set<String> processedThisSession = ConcurrentHashMap.newKeySet()

    /**
     * Safety net called on PROJECT_CHANGE_TYPE.LOAD — retries onNewFile in case it
     * fired before isProjectLoaded() became true (timing edge case on some versions).
     */
    void checkCurrentFile() {
        try {
            SourceTextEntry ste = Core.editor.currentEntry
            if (!ste) { LOG.info("checkCurrentFile: no current entry"); return }
            String file = ste.key.file
            LOG.info("checkCurrentFile: file=${file}")
            if (file) onNewFile(file)
        } catch (Exception e) {
            LOG.warning("checkCurrentFile error: ${e}")
        }
    }

    /** Primary trigger: OmegaT fires this reliably on startup and on file switch. */
    void onNewFile(String activeFileName) {
        LOG.info("onNewFile: ${activeFileName}")
        if (!activeFileName) return
        if (processedThisSession.contains(activeFileName)) {
            LOG.info("onNewFile: already processed, skipping")
            return
        }
        if (!Core.project.isProjectLoaded()) {
            LOG.info("onNewFile: project not loaded, skipping")
            return
        }
        processedThisSession.add(activeFileName)
        new Thread({ checkAndPrompt(activeFileName) }, "glossary-checker").start()
    }

    private void checkAndPrompt(String filePath) {
        try {
            if (!Core.project.isProjectLoaded()) return

            List<String> sourceStrings = collectSourceStrings(filePath)
            LOG.info("checkAndPrompt: filePath=${filePath} sourceStrings.size=${sourceStrings.size()}")
            if (!sourceStrings) return

            String srcLang = Core.project.projectProperties.sourceLanguage.language
            String tgtLang = Core.project.projectProperties.targetLanguage.language

            boolean needsExtraction = checkGlossaryStatus(sourceStrings, srcLang, tgtLang)
            LOG.info("checkAndPrompt: needsExtraction=${needsExtraction}")
            if (!needsExtraction) return

            String fileName = new File(filePath).name
            SwingUtilities.invokeLater {
                int choice = JOptionPane.showConfirmDialog(null,
                    "Extract glossary terms from \"${fileName}\"?\n" +
                    "Termium and OQLF will be searched for authoritative terminology.\n" +
                    "Suggestions will be saved to glossary/pending_glossary.txt for your review.",
                    "Glossary Extraction", JOptionPane.YES_NO_OPTION, JOptionPane.QUESTION_MESSAGE)
                if (choice == JOptionPane.YES_OPTION) {
                    new Thread({ runExtraction(filePath, sourceStrings, srcLang, tgtLang) },
                               "glossary-extractor").start()
                } else {
                    // Record the refusal so the DB shows 'deferred'; the popup will reappear next session.
                    new Thread({
                        try {
                            Util.httpPost(Util.GLOSSARY_DEFER_URL, [
                                source_strings: sourceStrings, source_lang: srcLang,
                                target_lang: tgtLang, file_path: filePath
                            ], 5_000)
                        } catch (Exception e) { LOG.warning("glossary_defer failed: ${e}") }
                    }, "glossary-defer").start()
                }
            }
        } catch (Exception e) {
            LOG.warning("checkAndPrompt error: ${e}")
        }
    }

    private List<String> collectSourceStrings(String filePath) {
        Core.project.projectFiles
            .find  { it.filePath == filePath }
            ?.entries
            ?.findAll { it.srcText?.trim() }
            ?.collect { it.srcText }
            ?: []
    }

    private boolean checkGlossaryStatus(List<String> sourceStrings, String srcLang, String tgtLang) {
        try {
            def resp = Util.parseJson(Util.httpPost(Util.GLOSSARY_STATUS_URL, [
                source_lang: srcLang, target_lang: tgtLang, source_strings: sourceStrings
            ], 10_000))
            resp.needs_extraction == true
        } catch (ignored) {
            false  // service unreachable — don't show popup
        }
    }

    /** Reads source terms from all glossary files in the project directory. */
    private List<String> loadExistingSourceTerms(String projectRoot) {
        def terms = []
        new File(projectRoot, "glossary").listFiles()?.each { f ->
            if (f.name ==~ /.*\.(txt|utf8|tab)/) {
                f.eachLine("UTF-8") { line ->
                    def col = line.split("\t")
                    if (col.length >= 2) terms << col[0].trim()
                }
            }
        }
        terms
    }

    private void runExtraction(String filePath, List<String> sourceStrings,
                               String srcLang, String tgtLang) {
        try {
            String projectRoot = Core.project.projectProperties.projectRoot
            List<String> existingTerms = loadExistingSourceTerms(projectRoot)

            def resp = Util.parseJson(Util.httpPost(Util.GLOSSARY_URL, [
                source_lang: srcLang, target_lang: tgtLang,
                source_strings: sourceStrings, existing_terms: existingTerms,
                file_path: filePath
            ], 120_000))

            List suggestions = resp.suggestions ?: []

            if (!suggestions) {
                SwingUtilities.invokeLater {
                    JOptionPane.showMessageDialog(null,
                        "No glossary suggestions found for this file.\n" +
                        "The model found no domain-specific terms worth adding.",
                        "Glossary Extraction Complete", JOptionPane.INFORMATION_MESSAGE)
                }
                return
            }

            new File(projectRoot, "glossary").mkdirs()
            new File(projectRoot, "glossary/pending_glossary.txt").withWriterAppend("UTF-8") { w ->
                suggestions.each { s ->
                    w.writeLine("${s.source}\t${s.target}${s.comment ? '\t' + s.comment : ''}")
                }
            }

            int count = suggestions.size()
            SwingUtilities.invokeLater {
                JOptionPane.showMessageDialog(null,
                    "${count} glossary suggestion${count == 1 ? '' : 's'} added to " +
                    "glossary/pending_glossary.txt.\n" +
                    "Review the file before merging into your main glossary.",
                    "Glossary Extraction Complete", JOptionPane.INFORMATION_MESSAGE)
            }
        } catch (Exception e) {
            SwingUtilities.invokeLater {
                JOptionPane.showMessageDialog(null, "Glossary extraction failed: ${e.message}",
                    "Glossary Extraction Error", JOptionPane.ERROR_MESSAGE)
            }
        }
    }
}

// ── Explicit listener implementations (map coercion is unreliable in OmegaT's classloader)

class GlossaryEntryListener implements IEntryEventListener {
    private final AiGlossaryPlugin plugin
    GlossaryEntryListener(AiGlossaryPlugin p) { this.plugin = p }

    @Override
    void onNewFile(String activeFileName) { plugin.onNewFile(activeFileName) }

    @Override
    void onEntryActivated(SourceTextEntry newEntry) { /* no-op */ }
}

class GlossaryProjectListener implements IProjectEventListener {
    private final AiGlossaryPlugin plugin
    GlossaryProjectListener(AiGlossaryPlugin p) { this.plugin = p }

    @Override
    void onProjectChanged(IProjectEventListener.PROJECT_CHANGE_TYPE eventType) {
        if (eventType == IProjectEventListener.PROJECT_CHANGE_TYPE.LOAD)
            plugin.checkCurrentFile()
    }
}

// ── Register event listeners and MT provider ──────────────────────────────────

new File("/tmp/omegat_groovy.txt").text = "ai_plugin.groovy ran at ${new Date()}\n"
java.util.logging.Logger.getLogger("AiGlossaryPlugin").warning("=== ai_plugin.groovy startup executing ===")

AiGlossaryPlugin plugin = new AiGlossaryPlugin()
CoreEvents.registerEntryEventListener(new GlossaryEntryListener(plugin))
CoreEvents.registerProjectChangeListener(new GlossaryProjectListener(plugin))

// OmegaT 6 builds the MT provider panel before firing the application startup
// event, so calling registerMachineTranslationClass immediately is too late.
// Deferring to invokeLater ensures the panel is refreshed after registration.
SwingUtilities.invokeLater {
    java.util.logging.Logger.getLogger("AiGlossaryPlugin").warning("=== registering AiTranslateProvider ===")
    Core.registerMachineTranslationClass(AiTranslateProvider)
}
