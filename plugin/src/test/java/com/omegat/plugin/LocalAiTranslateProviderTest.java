package com.omegat.plugin;

import org.junit.jupiter.api.Test;

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
}
