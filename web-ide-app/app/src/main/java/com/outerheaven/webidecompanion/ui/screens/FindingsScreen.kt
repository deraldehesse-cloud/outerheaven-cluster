package com.outerheaven.webidecompanion.ui.screens

import android.widget.Toast
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.webidecompanion.ui.theme.*
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel
import com.outerheaven.webidecompanion.ui.viewmodel.UiState

@Composable
fun FindingsScreen(
    viewModel: MainViewModel,
    modifier: Modifier = Modifier
) {
    val findingsState by viewModel.findingsState.collectAsState()
    val availablePackages by viewModel.availablePackages.collectAsState()
    val selectedPackage by viewModel.selectedPackage.collectAsState()
    val packageChipScrollState = rememberScrollState()
    val clipboardManager = LocalClipboardManager.current
    val context = LocalContext.current

    // Permanente Paketliste: Nutzt aggregierten ViewModel-Zustand, ergänzt um evtl. gewählte Pakete
    val packageList = remember(availablePackages, selectedPackage) {
        val list = availablePackages.toMutableList()
        selectedPackage?.let { sel ->
            if (sel.isNotBlank() && !list.contains(sel)) {
                list.add(sel)
            }
        }
        list
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBackground)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "LLM FINDINGS & ANALYSEN",
                        style = MaterialTheme.typography.titleLarge,
                        color = AccentCyan,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 1.sp
                    )
                    Surface(
                        color = AccentCyan.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp),
                        border = BorderStroke(1.dp, AccentCyan.copy(alpha = 0.6f))
                    ) {
                        Text(
                            text = "v1.0.6",
                            color = AccentCyan,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                        )
                    }
                }
                Text(
                    text = "Code-Auswertungen durch metal-coder:latest (DeepSeek 6.7B)",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
            IconButton(
                onClick = {
                    viewModel.loadSandboxOutputs()
                    viewModel.loadDropzone()
                    viewModel.loadFindings(selectedPackage)
                },
                modifier = Modifier
                    .clip(CircleShape)
                    .background(DarkSurfaceVariant)
            ) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Findings aktualisieren",
                    tint = AccentCyan
                )
            }
        }

        // 1. Permanente Chip-Leiste oben (bleibt auch bei 404 für neue APKs immer sichtbar)
        if (packageList.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(packageChipScrollState),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                packageList.forEach { pkg ->
                    val isSelected = pkg.equals(selectedPackage, ignoreCase = true)
                    FilterChip(
                        selected = isSelected,
                        onClick = { viewModel.selectPackage(pkg) },
                        label = { Text(pkg) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = AccentCyan,
                            selectedLabelColor = DarkBackground,
                            containerColor = DarkSurface,
                            labelColor = TextSecondary
                        ),
                        border = FilterChipDefaults.filterChipBorder(
                            enabled = true,
                            selected = isSelected,
                            borderColor = if (isSelected) AccentCyan else DarkBorder
                        )
                    )
                }
            }
        }

        // 2. Vollflächiger Markdown-Viewer oder 404-Fallback-Karte
        when (val state = findingsState) {
            is UiState.Success -> {
                val findings = state.data
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .border(1.dp, DarkBorder, RoundedCornerShape(10.dp)),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier.fillMaxSize()
                    ) {
                        // Toolbar im Markdown Viewer
                        Surface(
                            color = DarkSurfaceVariant,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 14.dp, vertical = 8.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Description,
                                        contentDescription = null,
                                        tint = AccentCyan,
                                        modifier = Modifier.size(18.dp)
                                    )
                                    Text(
                                        text = findings.filename.ifEmpty { "llm_findings.md" },
                                        style = MaterialTheme.typography.labelMedium,
                                        fontFamily = FontFamily.Monospace,
                                        color = TextPrimary,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Text(
                                        text = "(${findings.packageName})",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = TextMuted
                                    )
                                }

                                IconButton(
                                    onClick = {
                                        clipboardManager.setText(AnnotatedString(findings.content))
                                        Toast.makeText(context, "Findings kopiert", Toast.LENGTH_SHORT).show()
                                    },
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.ContentCopy,
                                        contentDescription = "Kopieren",
                                        tint = AccentCyan,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                            }
                        }

                        HorizontalDivider(color = DarkBorder)

                        // Scrollbarer strukturierter Markdown-Inhalt
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .weight(1f)
                                .verticalScroll(rememberScrollState())
                                .padding(16.dp)
                        ) {
                            MarkdownViewer(markdown = findings.content)
                        }
                    }
                }
            }
            is UiState.Loading -> {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = AccentCyan)
                }
            }
            is UiState.Error -> {
                val isNotFound = state.message == "NOT_FOUND" ||
                        state.message.contains("404") ||
                        state.message.contains("Keine Findings", ignoreCase = true)

                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .border(1.dp, DarkBorder, RoundedCornerShape(10.dp)),
                    colors = CardDefaults.cardColors(containerColor = DarkSurface)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState())
                            .padding(24.dp),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Icon(
                            imageVector = Icons.Default.Info,
                            contentDescription = null,
                            tint = if (isNotFound) AccentCyan else AccentOrange,
                            modifier = Modifier.size(48.dp)
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = if (isNotFound) "Keine LLM-Analyse vorhanden" else "Fehler beim Laden",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = if (isNotFound) AccentCyan else AccentOrange
                        )
                        if (!selectedPackage.isNullOrBlank()) {
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Paket: $selectedPackage",
                                style = MaterialTheme.typography.bodySmall,
                                color = TextMuted,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = if (isNotFound)
                                "Für '${selectedPackage ?: "dieses Paket"}' wurde noch keine llm_findings.md erzeugt.\nDie Analyse kann in der Sandbox oder Pipeline angestoßen werden."
                            else
                                state.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary,
                            textAlign = TextAlign.Center,
                            lineHeight = 20.sp
                        )
                        Spacer(modifier = Modifier.height(20.dp))
                        Button(
                            onClick = { viewModel.loadFindings(selectedPackage) },
                            colors = ButtonDefaults.buttonColors(containerColor = DarkSurfaceVariant),
                            border = BorderStroke(1.dp, DarkBorder)
                        ) {
                            Icon(
                                imageVector = Icons.Default.Refresh,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp),
                                tint = AccentCyan
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text("Erneut prüfen", color = TextPrimary)
                        }
                    }
                }
            }
            is UiState.Idle -> {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Text("Wähle ein Paket aus der oberen Leiste", color = TextSecondary)
                }
            }
        }
    }
}

/**
 * Vollflächiger Markdown-Viewer mit Unterstützung für:
 * - H1, H2, H3 Überschriften
 * - Horizontale Trennlinien (---)
 * - Codeblöcke (``` ... ```)
 * - Listen mit Bullet-Points (- / *)
 * - Formatierungen (**fett**, `code`)
 */
@Composable
fun MarkdownViewer(
    markdown: String,
    modifier: Modifier = Modifier
) {
    val lines = markdown.lines()
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        var inCodeBlock = false
        val currentCodeBlock = StringBuilder()

        for (line in lines) {
            val trimmed = line.trim()

            // Code-Block Start / Ende
            if (trimmed.startsWith("```")) {
                if (inCodeBlock) {
                    // Ende Codeblock
                    Surface(
                        color = DarkSurfaceVariant,
                        shape = RoundedCornerShape(6.dp),
                        border = BorderStroke(1.dp, DarkBorder),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(
                            text = currentCodeBlock.toString().trimEnd(),
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = FontFamily.Monospace,
                            color = AccentGreen,
                            modifier = Modifier
                                .fillMaxWidth()
                                .horizontalScroll(rememberScrollState())
                                .padding(10.dp)
                        )
                    }
                    currentCodeBlock.clear()
                    inCodeBlock = false
                } else {
                    inCodeBlock = true
                }
                continue
            }

            if (inCodeBlock) {
                currentCodeBlock.appendLine(line)
                continue
            }

            // Horizontale Trennlinie
            if (trimmed == "---" || trimmed == "***" || trimmed == "___") {
                HorizontalDivider(color = DarkBorder, modifier = Modifier.padding(vertical = 4.dp))
                continue
            }

            // Überschriften
            when {
                trimmed.startsWith("# ") -> {
                    Text(
                        text = trimmed.removePrefix("# ").trim(),
                        style = MaterialTheme.typography.titleLarge,
                        color = AccentCyan,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
                    )
                }
                trimmed.startsWith("## ") -> {
                    Text(
                        text = trimmed.removePrefix("## ").trim(),
                        style = MaterialTheme.typography.titleMedium,
                        color = AccentBlue,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 6.dp, bottom = 2.dp)
                    )
                }
                trimmed.startsWith("### ") -> {
                    Text(
                        text = trimmed.removePrefix("### ").trim(),
                        style = MaterialTheme.typography.titleSmall,
                        color = TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }
                trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                    val content = trimmed.substring(2).trim()
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.Top
                    ) {
                        Text(
                            text = "•",
                            color = AccentCyan,
                            fontWeight = FontWeight.Bold,
                            fontSize = 16.sp
                        )
                        Text(
                            text = parseInlineMarkdown(content),
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextPrimary,
                            lineHeight = 20.sp,
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
                trimmed.isEmpty() -> {
                    Spacer(modifier = Modifier.height(4.dp))
                }
                else -> {
                    Text(
                        text = parseInlineMarkdown(trimmed),
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextPrimary,
                        lineHeight = 22.sp
                    )
                }
            }
        }

        // Falls Datei mit ungeschlossenem Codeblock endet
        if (inCodeBlock && currentCodeBlock.isNotEmpty()) {
            Surface(
                color = DarkSurfaceVariant,
                shape = RoundedCornerShape(6.dp),
                border = BorderStroke(1.dp, DarkBorder),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = currentCodeBlock.toString().trimEnd(),
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                    color = AccentGreen,
                    modifier = Modifier.padding(10.dp)
                )
            }
        }
    }
}

/**
 * Parst inline **fett** und `code` Markups in einen AnnotatedString.
 */
fun parseInlineMarkdown(text: String): AnnotatedString {
    return buildAnnotatedString {
        var idx = 0
        while (idx < text.length) {
            val boldStart = text.indexOf("**", idx)
            val codeStart = text.indexOf("`", idx)

            val nextSpecial = when {
                boldStart != -1 && codeStart != -1 -> minOf(boldStart, codeStart)
                boldStart != -1 -> boldStart
                codeStart != -1 -> codeStart
                else -> -1
            }

            if (nextSpecial == -1) {
                append(text.substring(idx))
                break
            }

            if (nextSpecial > idx) {
                append(text.substring(idx, nextSpecial))
            }

            if (nextSpecial == boldStart) {
                val boldEnd = text.indexOf("**", boldStart + 2)
                if (boldEnd != -1) {
                    pushStyle(SpanStyle(fontWeight = FontWeight.Bold, color = AccentCyan))
                    append(text.substring(boldStart + 2, boldEnd))
                    pop()
                    idx = boldEnd + 2
                } else {
                    append("**")
                    idx = boldStart + 2
                }
            } else {
                val codeEnd = text.indexOf("`", codeStart + 1)
                if (codeEnd != -1) {
                    pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, color = AccentGreen, background = DarkSurfaceVariant))
                    append(" ${text.substring(codeStart + 1, codeEnd)} ")
                    pop()
                    idx = codeEnd + 1
                } else {
                    append("`")
                    idx = codeStart + 1
                }
            }
        }
    }
}
