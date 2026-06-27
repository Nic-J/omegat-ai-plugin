package com.omegat.plugin;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class LocalAiTranslateProviderTest {

    // ── resolveServiceBaseUrl ─────────────────────────────────────────────────
    // Preferences requires OmegaT's full runtime, which isn't bootstrapped in plain unit
    // tests, so this always exercises the fallback branch and returns the default.

    @Test
    void resolveServiceBaseUrl_fallsBackToDefaultOutsideOmegatRuntime() {
        assertEquals("http://localhost:8000", LocalAiTranslateProvider.resolveServiceBaseUrl());
    }

    // ── loadProjectStyleRules ─────────────────────────────────────────────────
    // Core.getProject() requires OmegaT's full runtime, which isn't bootstrapped in plain
    // unit tests, so this always exercises the fallback branch and returns null.

    @Test
    void loadProjectStyleRules_returnsNullOutsideOmegatRuntime() {
        assertNull(LocalAiTranslateProvider.loadProjectStyleRules());
    }

    // ── currentProjectId ──────────────────────────────────────────────────────
    // Core.getProject() requires OmegaT's full runtime, which isn't bootstrapped in plain
    // unit tests, so this always exercises the fallback branch and returns null.

    @Test
    void currentProjectId_returnsNullOutsideOmegatRuntime() {
        assertNull(LocalAiTranslateProvider.currentProjectId());
    }

    // ── extractStringField ────────────────────────────────────────────────────

    @Test
    void extractStringField_basic() {
        assertEquals("Bonjour",
            LocalAiTranslateProvider.extractStringField("{\"translated_text\":\"Bonjour\"}", "translated_text"));
    }

    @Test
    void extractStringField_absentField() {
        assertNull(LocalAiTranslateProvider.extractStringField("{\"foo\":\"bar\"}", "missing"));
    }

    @Test
    void extractStringField_escapedQuote() {
        assertEquals("say \"hello\"",
            LocalAiTranslateProvider.extractStringField("{\"text\":\"say \\\"hello\\\"\"}", "text"));
    }

    @Test
    void extractStringField_escapedNewline() {
        assertEquals("line1\nline2",
            LocalAiTranslateProvider.extractStringField("{\"text\":\"line1\\nline2\"}", "text"));
    }

    @Test
    void extractStringField_unicodeEscape() {
        assertEquals("café",
            LocalAiTranslateProvider.extractStringField("{\"text\":\"caf\\u00e9\"}", "text"));
    }

    // ── extractStringArray ────────────────────────────────────────────────────

    @Test
    void extractStringArray_basic() {
        List<String> result = LocalAiTranslateProvider.extractStringArray(
            "{\"qa_findings\":[\"Used approved term.\",\"Applied formal register.\"]}", "qa_findings");
        assertEquals(2, result.size());
        assertEquals("Used approved term.", result.get(0));
        assertEquals("Applied formal register.", result.get(1));
    }

    @Test
    void extractStringArray_empty() {
        List<String> result = LocalAiTranslateProvider.extractStringArray(
            "{\"qa_findings\":[]}", "qa_findings");
        assertTrue(result.isEmpty());
    }

    @Test
    void extractStringArray_absent() {
        List<String> result = LocalAiTranslateProvider.extractStringArray(
            "{\"translated_text\":\"Bonjour\"}", "qa_findings");
        assertTrue(result.isEmpty());
    }

    @Test
    void extractStringArray_withEscapedQuotes() {
        List<String> result = LocalAiTranslateProvider.extractStringArray(
            "{\"qa_findings\":[\"Use \\\"Enregistrer\\\" not \\\"Sauvegarder\\\".\"]}", "qa_findings");
        assertEquals(1, result.size());
        assertEquals("Use \"Enregistrer\" not \"Sauvegarder\".", result.get(0));
    }

    // ── extractBooleanField ───────────────────────────────────────────────────

    @Test
    void extractBooleanField_true() {
        assertTrue(LocalAiTranslateProvider.extractBooleanField(
            "{\"needs_extraction\":true}", "needs_extraction"));
    }

    @Test
    void extractBooleanField_false() {
        assertFalse(LocalAiTranslateProvider.extractBooleanField(
            "{\"needs_extraction\":false}", "needs_extraction"));
    }

    @Test
    void extractBooleanField_absent() {
        assertFalse(LocalAiTranslateProvider.extractBooleanField("{}", "needs_extraction"));
    }

    // ── quoted ────────────────────────────────────────────────────────────────

    @Test
    void quoted_null() {
        assertEquals("null", LocalAiTranslateProvider.quoted(null));
    }

    @Test
    void quoted_plain() {
        assertEquals("\"hello\"", LocalAiTranslateProvider.quoted("hello"));
    }

    @Test
    void quoted_escapesNewlineAndQuote() {
        assertEquals("\"line1\\nline2\"", LocalAiTranslateProvider.quoted("line1\nline2"));
        assertEquals("\"say \\\"hi\\\"\"", LocalAiTranslateProvider.quoted("say \"hi\""));
        assertEquals("\"a\\\\b\"", LocalAiTranslateProvider.quoted("a\\b"));
    }

    // ── buildGlossaryJson ─────────────────────────────────────────────────────

    @Test
    void buildGlossaryJson_withFilePath() {
        String json = LocalAiTranslateProvider.GlossaryExtractionListener.buildGlossaryJson(
            Arrays.asList("Save", "Open"), "EN", "FR-CA", "docs/guide.docx", null);
        assertTrue(json.contains("\"file_path\":\"docs/guide.docx\""));
        assertTrue(json.contains("\"source_strings\":[\"Save\",\"Open\"]"));
        assertFalse(json.contains("existing_terms"));
    }

    @Test
    void buildGlossaryJson_withExistingTerms() {
        String json = LocalAiTranslateProvider.GlossaryExtractionListener.buildGlossaryJson(
            Collections.singletonList("Open"), "EN", "FR-CA", null, Arrays.asList("software", "file"));
        assertTrue(json.contains("\"existing_terms\":[\"software\",\"file\"]"));
        assertFalse(json.contains("file_path"));
    }

    @Test
    void buildGlossaryJson_noOptionalFields() {
        String json = LocalAiTranslateProvider.GlossaryExtractionListener.buildGlossaryJson(
            Collections.singletonList("Open"), "EN", "FR-CA", null, null);
        assertFalse(json.contains("file_path"));
        assertFalse(json.contains("existing_terms"));
    }

    // ── buildTranslateJson (style_rules delivery) ─────────────────────────────
    // Uses the median point (·) from gender-inclusive French forms. The expected and
    // actual strings reuse the same `styleRules` variable, so the assertion is immune to
    // source-file encoding either way.

    @Test
    void buildTranslateJson_includesStyleRulesWhenPresent() {
        String styleRules = "Use the median point for gender-inclusive forms, e.g. directeur·trice·s.";
        String json = LocalAiTranslateProvider.buildTranslateJson(
            "the directors", "EN", "FR-CA", null,
            Collections.emptyList(), Collections.emptyList(),
            Collections.emptyList(), Collections.emptyList(),
            styleRules, null);
        assertTrue(json.contains("\"style_rules\":\"" + styleRules + "\""),
            "style_rules must be serialized into the translate request when provided");
    }

    @Test
    void buildTranslateJson_omitsStyleRulesWhenNull() {
        String json = LocalAiTranslateProvider.buildTranslateJson(
            "the directors", "EN", "FR-CA", null,
            Collections.emptyList(), Collections.emptyList(),
            Collections.emptyList(), Collections.emptyList(),
            null, null);
        assertFalse(json.contains("style_rules"));
    }

    // ── parseSuggestions ──────────────────────────────────────────────────────

    @Test
    void parseSuggestions_basic() {
        String json = "{\"suggestions\":["
            + "{\"source\":\"software\",\"target\":\"logiciel\",\"comment\":\"standard FR-CA\"},"
            + "{\"source\":\"file\",\"target\":\"fichier\"}"
            + "]}";
        List<String[]> result = LocalAiTranslateProvider.GlossaryExtractionListener.parseSuggestions(json);
        assertEquals(2, result.size());
        assertArrayEquals(new String[]{ "software", "logiciel", "standard FR-CA" }, result.get(0));
        assertArrayEquals(new String[]{ "file", "fichier", null }, result.get(1));
    }

    @Test
    void parseSuggestions_empty() {
        assertEquals(0, LocalAiTranslateProvider.GlossaryExtractionListener
            .parseSuggestions("{\"suggestions\":[]}").size());
    }

    @Test
    void parseSuggestions_missingTarget_skipped() {
        String json = "{\"suggestions\":[{\"source\":\"foo\"}]}";
        assertEquals(0, LocalAiTranslateProvider.GlossaryExtractionListener.parseSuggestions(json).size());
    }

    // ── findNearMissStyleRulesFile ─────────────────────────────────────────────
    // Pure filesystem logic — no OmegaT runtime needed.

    @Test
    void findNearMissStyleRulesFile_detectsStyleRulesTxt(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("style_rules.txt"));
        Path result = LocalAiTranslateProvider.findNearMissStyleRulesFile(dir);
        assertNotNull(result);
        assertEquals("style_rules.txt", result.getFileName().toString());
    }

    @Test
    void findNearMissStyleRulesFile_ignoresCanonicalName(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("ai_style_rules.txt"));
        assertNull(LocalAiTranslateProvider.findNearMissStyleRulesFile(dir));
    }

    @Test
    void findNearMissStyleRulesFile_ignoresExampleFile(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("ai_style_rules.example.txt"));
        assertNull(LocalAiTranslateProvider.findNearMissStyleRulesFile(dir));
    }

    @Test
    void findNearMissStyleRulesFile_detectsMarkdownVariant(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("ai_style_rules.md"));
        assertNotNull(LocalAiTranslateProvider.findNearMissStyleRulesFile(dir));
    }

    @Test
    void findNearMissStyleRulesFile_returnsNullWhenNoMatch(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("omegat.project"));
        Files.createFile(dir.resolve("pending_glossary.txt"));
        assertNull(LocalAiTranslateProvider.findNearMissStyleRulesFile(dir));
    }

    @Test
    void findNearMissStyleRulesFile_caseInsensitive(@TempDir Path dir) throws Exception {
        Files.createFile(dir.resolve("Style_Rules.TXT"));
        assertNotNull(LocalAiTranslateProvider.findNearMissStyleRulesFile(dir));
    }
}
