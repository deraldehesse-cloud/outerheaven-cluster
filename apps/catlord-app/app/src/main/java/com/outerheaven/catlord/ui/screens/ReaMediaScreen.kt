package com.outerheaven.catlord.ui.screens

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.automirrored.filled.Send
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
import com.outerheaven.catlord.speech.SpeechState
import com.outerheaven.catlord.ui.AppScreen
import com.outerheaven.catlord.ui.MainViewModel
import com.outerheaven.catlord.ui.theme.*

@Composable
fun ReaMediaScreen(viewModel: MainViewModel) {
    val mediaStats by viewModel.mediaStats.collectAsState()
    val findings by viewModel.latestFindings.collectAsState()
    val isConnected by viewModel.isConnected.collectAsState()
    val isLoading by viewModel.isLoadingReaMedia.collectAsState()
    val serverUrl by viewModel.serverUrl.collectAsState()

    // Story & Game Assistant State
    val selectedPackage by viewModel.selectedPackage.collectAsState()
    val availablePackages by viewModel.availablePackages.collectAsState()
    val deepSeekPrompt by viewModel.deepSeekPrompt.collectAsState()
    val isAskingDeepSeek by viewModel.isAskingDeepSeek.collectAsState()
    val deepSeekResponse by viewModel.deepSeekResponse.collectAsState()
    val speechState by viewModel.speechState.collectAsState()

    val packageChipScrollState = rememberScrollState()
    val templateScrollState = rememberScrollState()

    val context = LocalContext.current
    val clipboardManager = LocalClipboardManager.current

    var showUrlDialog by remember { mutableStateOf(false) }
    var tempUrl by remember { mutableStateOf(serverUrl) }

    LaunchedEffect(Unit) {
        viewModel.loadMediaAndFindings()
        viewModel.checkConnection()
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Cluster Connection Header
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(14.dp)
                                .background(if (isConnected) NeonGreen else DangerRed, CircleShape)
                        )
                        Column {
                            Text(
                                text = if (isConnected) "Metal-Bridge Verbunden" else "Metal-Bridge Getrennt",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = serverUrl,
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                        }
                    }

                    Row {
                        IconButton(onClick = {
                            tempUrl = serverUrl
                            showUrlDialog = true
                        }) {
                            Icon(Icons.Default.Settings, contentDescription = "Einstellungen", tint = TextSecondary)
                        }

                        IconButton(onClick = { viewModel.loadMediaAndFindings() }) {
                            Icon(Icons.Default.Refresh, contentDescription = "Aktualisieren", tint = NeonCyan)
                        }
                    }
                }
            }
        }

        // Section: Elliot & Käch Datasets / Media Stats
        item {
            Text(
                text = "🐾 Datasets & Media (Elliot & Käch)",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
        }

        if (isLoading && mediaStats == null) {
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = NeonCyan)
                }
            }
        } else {
            val stats = mediaStats
            val totals = stats?.totals

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    // Elliot Card
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkSurface),
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                Icon(Icons.Default.Pets, contentDescription = null, tint = AmberGold, modifier = Modifier.size(20.dp))
                                Text("Elliot (Tank)", fontWeight = FontWeight.Bold, color = AmberGold, fontSize = 14.sp)
                            }
                            Text(
                                text = "${totals?.elliotImages ?: 0} Bilder",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = "${totals?.elliotVideos ?: 0} Videos",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                            Text(
                                text = stats?.details?.get("elliot")?.sizeFormatted ?: "",
                                style = MaterialTheme.typography.labelSmall,
                                color = TextDisabled
                            )
                        }
                    }

                    // Käch Card
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkSurface),
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                Icon(Icons.Default.SportsMartialArts, contentDescription = null, tint = NeonCyan, modifier = Modifier.size(20.dp))
                                Text("Käch / Cash", fontWeight = FontWeight.Bold, color = NeonCyan, fontSize = 14.sp)
                            }
                            Text(
                                text = "${totals?.kaechImages ?: 0} Bilder",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = "${totals?.kaechVideos ?: 0} Videos",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                            Text(
                                text = stats?.details?.get("kaech")?.sizeFormatted ?: "",
                                style = MaterialTheme.typography.labelSmall,
                                color = TextDisabled
                            )
                        }
                    }
                }
            }

            // Raw Footage & Gesamt
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkSurfaceVariant),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text("Gesamtes Catlord-Archiv", fontWeight = FontWeight.Bold, color = TextPrimary)
                            Text(
                                text = "NFS: /mnt/storage-beta/projects/catlord-media/",
                                color = TextSecondary,
                                fontSize = 11.sp
                            )
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(
                                text = "${totals?.totalImages ?: 0} Fotos • ${totals?.totalVideos ?: 0} Videos",
                                fontWeight = FontWeight.Bold,
                                color = NeonGreen
                            )
                            Text(
                                text = stats?.details?.get("raw_footage")?.sizeFormatted ?: "",
                                color = TextDisabled,
                                fontSize = 11.sp
                            )
                        }
                    }
                }
            }
        }

        // 3. Section: Catlord Assets & Lore Quick Access
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, DarkSurfaceElevated, RoundedCornerShape(16.dp))
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Icon(Icons.AutoMirrored.Filled.MenuBook, contentDescription = null, tint = AmberGold)
                            Text(
                                text = "Catlord Lore & Dokumente",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                        }
                        Surface(
                            color = AmberGold.copy(alpha = 0.2f),
                            shape = RoundedCornerShape(6.dp)
                        ) {
                            Text(
                                text = "DOCS",
                                color = AmberGold,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                            )
                        }
                    }

                    Text(
                        text = "Direkter Zugriff auf Spiellogik, Charakter-Profile & Sprachaufnahmen:",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        // Charaktere
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier
                                .weight(1f)
                                .clickable {
                                    viewModel.openDoc("docs", "charaktere.md")
                                    viewModel.setScreen(AppScreen.LORE_VIEWER)
                                }
                        ) {
                            Column(
                                modifier = Modifier.padding(10.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(Icons.Default.People, contentDescription = null, tint = AmberGold, modifier = Modifier.size(22.dp))
                                Text("Charaktere", fontWeight = FontWeight.Bold, fontSize = 12.sp, color = TextPrimary)
                                Text("charaktere.md", fontSize = 10.sp, color = TextSecondary)
                            }
                        }

                        // Gameplay
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier
                                .weight(1f)
                                .clickable {
                                    viewModel.openDoc("docs", "gameplay.md")
                                    viewModel.setScreen(AppScreen.LORE_VIEWER)
                                }
                        ) {
                            Column(
                                modifier = Modifier.padding(10.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(Icons.Default.SportsEsports, contentDescription = null, tint = NeonCyan, modifier = Modifier.size(22.dp))
                                Text("Gameplay", fontWeight = FontWeight.Bold, fontSize = 12.sp, color = TextPrimary)
                                Text("gameplay.md", fontSize = 10.sp, color = TextSecondary)
                            }
                        }

                        // Geschichte / Lore
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier
                                .weight(1f)
                                .clickable {
                                    viewModel.openDoc("docs", "geschichte.md")
                                    viewModel.setScreen(AppScreen.LORE_VIEWER)
                                }
                        ) {
                            Column(
                                modifier = Modifier.padding(10.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(Icons.Default.AutoStories, contentDescription = null, tint = CyberViolet, modifier = Modifier.size(22.dp))
                                Text("Storyline", fontWeight = FontWeight.Bold, fontSize = 12.sp, color = TextPrimary)
                                Text("geschichte.md", fontSize = 10.sp, color = TextSecondary)
                            }
                        }

                        // Voice Ingest
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier
                                .weight(1f)
                                .clickable {
                                    viewModel.setScreen(AppScreen.VOICE_INGEST)
                                }
                        ) {
                            Column(
                                modifier = Modifier.padding(10.dp),
                                horizontalAlignment = Alignment.CenterHorizontally,
                                verticalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                Icon(Icons.Default.Mic, contentDescription = null, tint = NeonGreen, modifier = Modifier.size(22.dp))
                                Text("Voice Ingest", fontWeight = FontWeight.Bold, fontSize = 12.sp, color = TextPrimary)
                                Text("Aufnahme", fontSize = 10.sp, color = TextSecondary)
                            }
                        }
                    }
                }
            }
        }

        // 4. Section: Story- & Game-Assistent (DeepSeek AI Co-Writer)
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, CyberViolet.copy(alpha = 0.5f), RoundedCornerShape(16.dp))
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Icon(Icons.Default.AutoAwesome, contentDescription = null, tint = CyberViolet)
                            Text(
                                text = "Story- & Game-Assistent",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                        }
                        Surface(
                            color = CyberViolet.copy(alpha = 0.2f),
                            shape = RoundedCornerShape(6.dp)
                        ) {
                            Text(
                                text = "DEEPSEEK CO-WRITER",
                                color = CyberViolet,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                            )
                        }
                    }

                    Text(
                        text = "Spiellogik, Dialoge (Elliot & Käch), Quests & Spielcode für Catlord generieren:",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary
                    )

                    // Topic Chips
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(packageChipScrollState),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        availablePackages.forEach { pkg ->
                            val isSelected = pkg.equals(selectedPackage, ignoreCase = true)
                            FilterChip(
                                selected = isSelected,
                                onClick = { viewModel.selectPackage(pkg) },
                                label = { Text(pkg, fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal) },
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

                    // Prompt Vorlagen (Templates)
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .horizontalScroll(templateScrollState),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        SuggestionChip(
                            onClick = {
                                viewModel.setDeepSeekPrompt("Schreibe einen Dialog zwischen dem faulen, zynischen Kater Elliot und der agilen Ninja-Katze Käch über ")
                            },
                            label = { Text("💬 Elliot & Käch Dialog", fontSize = 11.sp) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextPrimary
                            )
                        )
                        SuggestionChip(
                            onClick = {
                                viewModel.setDeepSeekPrompt("Entwirf eine Quest im Katzenklo-Dungeon mit skurrilen Gegnern, Bosskampf und Loot: ")
                            },
                            label = { Text("⚔️ Katzenklo Quest", fontSize = 11.sp) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextPrimary
                            )
                        )
                        SuggestionChip(
                            onClick = {
                                viewModel.setDeepSeekPrompt("Beschreibe eine neue Charakter-Evolution von Elliot mit Lichtgeschwindigkeits-Fähigkeit: ")
                            },
                            label = { Text("🐾 Evolution", fontSize = 11.sp) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextPrimary
                            )
                        )
                        SuggestionChip(
                            onClick = {
                                viewModel.setDeepSeekPrompt("Schreibe Python-Code für die Catlord Idle-Worker Mechanik und Score-Berechnung: ")
                            },
                            label = { Text("🐍 Game Code", fontSize = 11.sp) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextPrimary
                            )
                        )
                    }

                    // Input Field
                    OutlinedTextField(
                        value = deepSeekPrompt,
                        onValueChange = { viewModel.setDeepSeekPrompt(it) },
                        label = { Text("Story-Prompt / Dialog- oder Code-Frage") },
                        placeholder = { Text("z.B. Wie reagiert Elliot, wenn Käch sein Trockenfutter stiehlt?") },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 2,
                        maxLines = 5,
                        trailingIcon = {
                            if (deepSeekPrompt.isNotEmpty()) {
                                IconButton(onClick = { viewModel.setDeepSeekPrompt("") }) {
                                    Icon(Icons.Default.Clear, contentDescription = "Leeren", tint = TextSecondary)
                                }
                            }
                        },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = CyberViolet,
                            unfocusedBorderColor = DarkSurfaceElevated,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedLabelColor = CyberViolet,
                            unfocusedLabelColor = TextSecondary
                        )
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        val isListening = speechState is SpeechState.Listening

                        // Speech Input Button
                        IconButton(
                            onClick = { viewModel.toggleDeepSeekSpeech() },
                            modifier = Modifier
                                .background(
                                    if (isListening) NeonGreen.copy(alpha = 0.2f) else DarkSurfaceVariant,
                                    CircleShape
                                )
                                .border(
                                    1.dp,
                                    if (isListening) NeonGreen else DarkSurfaceElevated,
                                    CircleShape
                                )
                        ) {
                            Icon(
                                Icons.Default.Mic,
                                contentDescription = "Einsprechen",
                                tint = if (isListening) NeonGreen else TextSecondary
                            )
                        }

                        // Send Button
                        Button(
                            onClick = { viewModel.askDeepSeek() },
                            enabled = !isAskingDeepSeek && deepSeekPrompt.isNotBlank(),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = CyberViolet,
                                contentColor = DarkBackground
                            ),
                            modifier = Modifier.weight(1f)
                        ) {
                            if (isAskingDeepSeek) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    color = DarkBackground,
                                    strokeWidth = 2.dp
                                )
                                Spacer(Modifier.width(8.dp))
                                Text("DeepSeek dichtet...", fontWeight = FontWeight.Bold)
                            } else {
                                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(6.dp))
                                Text("Generieren & Fragen", fontWeight = FontWeight.Bold)
                            }
                        }
                    }

                    // Loading State
                    if (isAskingDeepSeek) {
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier.padding(14.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                CircularProgressIndicator(color = CyberViolet, modifier = Modifier.size(20.dp))
                                Text(
                                    text = "DeepSeek generiert auf metal-dgear (CPU, ca. 20–60 Sek.)...",
                                    fontSize = 12.sp,
                                    color = TextSecondary
                                )
                            }
                        }
                    }

                    // DeepSeek Response Card
                    deepSeekResponse?.let { resp ->
                        Card(
                            colors = CardDefaults.cardColors(containerColor = DarkSurfaceElevated),
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Column(
                                modifier = Modifier.padding(14.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "Antwort vom Story-Assistenten",
                                        fontWeight = FontWeight.Bold,
                                        color = NeonGreen,
                                        fontSize = 13.sp
                                    )
                                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                        IconButton(
                                            onClick = {
                                                clipboardManager.setText(AnnotatedString(resp.response))
                                                Toast.makeText(context, "Text in Zwischenablage kopiert", Toast.LENGTH_SHORT).show()
                                            },
                                            modifier = Modifier.size(24.dp)
                                        ) {
                                            Icon(Icons.Default.ContentCopy, contentDescription = "Kopieren", tint = TextSecondary, modifier = Modifier.size(16.dp))
                                        }
                                        IconButton(
                                            onClick = { viewModel.clearDeepSeekResponse() },
                                            modifier = Modifier.size(24.dp)
                                        ) {
                                            Icon(Icons.Default.Close, contentDescription = "Schließen", tint = TextSecondary, modifier = Modifier.size(16.dp))
                                        }
                                    }
                                }

                                if (resp.savedToFindings) {
                                    Text(
                                        text = "💾 Gespeichert in: projects/catlord/rea-findings/${resp.fileName ?: ""}",
                                        fontSize = 11.sp,
                                        color = AmberGold
                                    )
                                }

                                HorizontalDivider(color = DarkSurface)

                                Text(
                                    text = resp.response,
                                    color = TextPrimary,
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 12.sp,
                                    lineHeight = 18.sp
                                )
                            }
                        }
                    }
                }
            }
        }

        // 5. Section: Findings & Story Notizen Viewer
        item {
            findings?.let { f ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkSurface),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = f.filename ?: "llm_findings.md",
                                fontWeight = FontWeight.Bold,
                                color = AmberGold
                            )
                            Text(
                                text = f.pkg ?: selectedPackage,
                                color = CyberViolet,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold
                            )
                        }

                        if (!f.modifiedAt.isNullOrBlank()) {
                            Text(
                                text = "Zuletzt geändert: ${f.modifiedAt.take(19).replace('T', ' ')}",
                                color = TextDisabled,
                                fontSize = 11.sp
                            )
                        }

                        HorizontalDivider(color = DarkSurfaceElevated)

                        Text(
                            text = f.content ?: "Kein Inhalt vorhanden.",
                            color = TextPrimary,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            lineHeight = 18.sp
                        )
                    }
                }
            }
        }
    }

    // Server-URL Konfigurations-Dialog
    if (showUrlDialog) {
        AlertDialog(
            onDismissRequest = { showUrlDialog = false },
            title = { Text("Metal-Bridge Server-URL") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Tailscale IP oder Hostname von metal-dgear:")
                    OutlinedTextField(
                        value = tempUrl,
                        onValueChange = { tempUrl = it },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            },
            confirmButton = {
                Button(onClick = {
                    viewModel.updateServerUrl(tempUrl)
                    showUrlDialog = false
                }) {
                    Text("Speichern")
                }
            },
            dismissButton = {
                TextButton(onClick = { showUrlDialog = false }) {
                    Text("Abbrechen")
                }
            }
        )
    }
}
