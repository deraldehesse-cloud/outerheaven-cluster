package com.outerheaven.catlord.data.api

import com.outerheaven.catlord.data.model.*
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

interface CatlordApiService {

    @GET("api/status")
    suspend fun getStatus(): ClusterStatusResponse

    @POST("api/catlord/voice-note")
    suspend fun postVoiceNote(@Body req: VoiceNotePostRequest): VoiceNotePostResponse

    @GET("api/catlord/docs")
    suspend fun getDocs(@Query("folder") folder: String? = null): DocsListResponse

    @GET("api/catlord/docs/{folder}/{filename}")
    suspend fun getDocContent(
        @Path("folder") folder: String,
        @Path("filename") filename: String
    ): DocDetailResponse

    @GET("api/catlord/media-stats")
    suspend fun getMediaStats(): MediaStatsResponse

    @GET("api/findings/latest")
    suspend fun getLatestFindings(@Query("package") pkg: String? = null): FindingsResponse

    @GET("api/dropzone")
    suspend fun getDropzone(): DropzoneResponse

    @POST("api/sandbox/decompile")
    suspend fun decompileApk(@Body req: DecompileRequest): DecompileResponse

    @POST("api/catlord/ask-deepseek")
    suspend fun askDeepSeek(@Body req: AskDeepSeekRequest): AskDeepSeekResponse

    companion object {
        const val DEFAULT_BASE_URL = "http://100.96.88.84:8444/"

        fun create(baseUrl: String = DEFAULT_BASE_URL): CatlordApiService {
            val logging = HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            }

            val client = OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(180, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(logging)
                .build()

            val cleanUrl = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"

            return Retrofit.Builder()
                .baseUrl(cleanUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
                .create(CatlordApiService::class.java)
        }
    }
}
