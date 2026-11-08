package com.outerheaven.catlord.ui.screens

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.outerheaven.catlord.data.model.DocItem
import com.outerheaven.catlord.ui.MainViewModel
import com.outerheaven.catlord.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoreViewerScreen(viewModel: MainViewModel) {
    val docsList by viewModel.docsList.collectAsState()
    val selectedFolder by viewModel.selectedFolderFilter.collectAsState()
    val searchQuery by viewModel.searchQuery.collectAsState()
    val selectedDoc by viewModel.selectedDoc.collectAsState()
    val isLoading by viewModel.isLoadingDocs.collectAsState()

    val context = LocalContext.current
    val clipboardManager = LocalClipboardManager.current

    val folders = listOf(
        null to "Alle",
        "docs" to "Docs",
        "prompts" to "Prompts",
        "voice-notes" to "Voice Notes",
        "rea-findings" to "REA Findings"
    )

    val filteredDocs = remember(docsList, searchQuery) {
        if (searchQuery.isBlank()) {
            docsList
        } else {
            val q = searchQuery.trim().lowercase()
            docsList.filter {
                it.title.lowercase().contains(q) ||
                it.filename.lowercase().contains(q) ||
                it.folder.lowercase().contains(q)
            }
        }
    }

    LaunchedEffect(Unit) {
        viewModel.loadDocs()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        // Search & Refresh Row
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            OutlinedTextField(
                value = searchQuery,
                onValueChange = { viewModel.setSearchQuery(it) },
                placeholder = { Text("Dokumente & Lore durchsuchen...") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null, tint = TextSecondary) },
                trailingIcon = {
                    if (searchQuery.isNotBlank()) {
                        IconButton(onClick = { viewModel.setSearchQuery("") }) {
                            Icon(Icons.Default.Clear, contentDescription = "Löschen", tint = TextSecondary)
                        }
                    }
                },
                singleLine = true,
                modifier = Modifier.weight(1f),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = NeonCyan,
                    unfocusedBorderColor = DarkSurfaceElevated,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary
                )
            )

            IconButton(
                onClick = { viewModel.loadDocs() },
                modifier = Modifier
                    .background(DarkSurfaceVariant, RoundedCornerShape(12.dp))
                    .size(50.dp)
            ) {
                Icon(Icons.Default.Refresh, contentDescription = "Aktualisieren", tint = NeonCyan)
            }
        }

        // Folder Filter Chips
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            folders.forEach { (key, label) ->
                val isSelected = selectedFolder == key
                FilterChip(
                    selected = isSelected,
                    onClick = { viewModel.setFolderFilter(key) },
                    label = { Text(label) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = CyberViolet,
                        selectedLabelColor = DarkBackground,
                        containerColor = DarkSurfaceVariant,
                        labelColor = TextPrimary
                    ),
                    border = FilterChipDefaults.filterChipBorder(
                        enabled = true,
                        selected = isSelected,
                        borderColor = if (isSelected) CyberViolet else DarkSurfaceElevated
                    )
                )
            }
        }

        if (isLoading) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(32.dp),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator(color = NeonCyan)
            }
        } else if (filteredDocs.isEmpty()) {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(32.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(Icons.Default.FolderOpen, contentDescription = null, tint = TextSecondary, modifier = Modifier.size(40.dp))
                    Text("Keine Dokumente gefunden.", color = TextSecondary)
                    Text("Pfade: projects/catlord/docs, prompts, voice-notes, rea-findings", color = TextDisabled, fontSize = 11.sp)
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(filteredDocs, key = { it.relativePath }) { doc ->
                    DocItemCard(
                        doc = doc,
                        onClick = { viewModel.openDoc(doc.folder, doc.filename) }
                    )
                }
            }
        }
    }

    // Detailansicht / Markdown Viewer Dialog
    selectedDoc?.let { doc ->
        Dialog(
            onDismissRequest = { viewModel.closeDocViewer() },
            properties = DialogProperties(usePlatformDefaultWidth = false)
        ) {
            Surface(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                shape = RoundedCornerShape(20.dp),
                color = DarkBackground,
                border = androidx.compose.foundation.BorderStroke(1.dp, DarkSurfaceElevated)
            ) {
                Column(modifier = Modifier.fillMaxSize()) {
                    // Dialog Header
                    Surface(color = DarkSurface, modifier = Modifier.fillMaxWidth()) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(16.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Surface(
                                    color = CyberViolet.copy(alpha = 0.2f),
                                    shape = RoundedCornerShape(4.dp)
                                ) {
                                    Text(
                                        text = doc.folder.uppercase(),
                                        color = CyberViolet,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Bold,
                                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                    )
                                }
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    text = doc.filename,
                                    style = MaterialTheme.typography.titleMedium,
                                    fontWeight = FontWeight.Bold,
                                    color = TextPrimary
                                )
                                Text(
                                    text = "${doc.sizeFormatted} • ${doc.relativePath}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = TextSecondary
                                )
                            }

                            Row {
                                IconButton(onClick = {
                                    clipboardManager.setText(AnnotatedString(doc.content))
                                    Toast.makeText(context, "In die Zwischenablage kopiert", Toast.LENGTH_SHORT).show()
                                }) {
                                    Icon(Icons.Default.ContentCopy, contentDescription = "Kopieren", tint = NeonCyan)
                                }
                                IconButton(onClick = { viewModel.closeDocViewer() }) {
                                    Icon(Icons.Default.Close, contentDescription = "Schließen", tint = TextPrimary)
                                }
                            }
                        }
                    }

                    HorizontalDivider(color = DarkSurfaceElevated)

                    // Dialog Content
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .padding(16.dp)
                            .verticalScroll(rememberScrollState())
                    ) {
                        Text(
                            text = doc.content,
                            color = TextPrimary,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 13.sp,
                            lineHeight = 20.sp
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun DocItemCard(
    doc: DocItem,
    onClick: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = DarkSurface),
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Box(
                modifier = Modifier
                    .size(42.dp)
                    .background(
                        when (doc.folder) {
                            "docs" -> CyberViolet.copy(alpha = 0.2f)
                            "prompts" -> AmberGold.copy(alpha = 0.2f)
                            "voice-notes" -> NeonCyan.copy(alpha = 0.2f)
                            else -> NeonGreen.copy(alpha = 0.2f)
                        },
                        RoundedCornerShape(8.dp)
                    ),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = when (doc.folder) {
                        "docs" -> Icons.Default.Description
                        "prompts" -> Icons.Default.Terminal
                        "voice-notes" -> Icons.Default.Mic
                        else -> Icons.Default.Science
                    },
                    contentDescription = null,
                    tint = when (doc.folder) {
                        "docs" -> CyberViolet
                        "prompts" -> AmberGold
                        "voice-notes" -> NeonCyan
                        else -> NeonGreen
                    },
                    modifier = Modifier.size(22.dp)
                )
            }

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = doc.title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = TextPrimary,
                    maxLines = 1
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = "${doc.folder} • ${doc.filename}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary,
                    fontSize = 12.sp,
                    maxLines = 1
                )
            }

            Column(horizontalAlignment = Alignment.End) {
                Text(
                    text = doc.sizeFormatted,
                    style = MaterialTheme.typography.labelSmall,
                    color = TextDisabled
                )
                Icon(Icons.Default.ChevronRight, contentDescription = null, tint = TextSecondary, modifier = Modifier.size(18.dp))
            }
        }
    }
}
